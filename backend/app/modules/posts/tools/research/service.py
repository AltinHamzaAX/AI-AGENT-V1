import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from time import monotonic

from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.domain.observability import safe_error_code
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    LLMProvider,
    ProviderError,
    ProviderQuotaError,
    ResearchProvider,
)

from .cache import InMemoryResearchCache, ResearchCache
from .metrics import (
    ResearchCategoryMetrics,
    ResearchMetricsSink,
    ResearchStageMetrics,
)
from .schemas import (
    ExternalResearchInput,
    ExternalResearchResult,
    ResearchCategory,
    ResearchConfidence,
    ResearchContext,
    ResearchReport,
    ResearchStatus,
)
from .tools import (
    BaseResearchTool,
    default_research_tools,
    locality_cache_key,
    normalize_research_query,
    research_cache_key,
)

logger = logging.getLogger(__name__)


class ExternalResearchService:
    """Runs independent research tools concurrently and caches each report."""

    def __init__(
        self,
        tools: Iterable[BaseResearchTool],
        *,
        cache: ResearchCache | None = None,
        cache_ttl_seconds: int = 3_600,
        max_concurrency: int = 4,
        tool_timeout_seconds: float = 180.0,
        stage_timeout_seconds: float = 300.0,
        metrics_sink: ResearchMetricsSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tools = tuple(tools)
        categories = [tool.category for tool in self._tools]
        if set(categories) != set(ResearchCategory) or len(categories) != len(ResearchCategory):
            raise ValueError("external research requires exactly one tool per category")
        if not 1 <= max_concurrency <= len(ResearchCategory):
            raise ValueError("research max_concurrency must be between 1 and 8")
        if cache_ttl_seconds <= 0:
            raise ValueError("research cache TTL must be positive")
        if tool_timeout_seconds <= 0:
            raise ValueError("research tool timeout must be positive")
        if stage_timeout_seconds < tool_timeout_seconds:
            raise ValueError("research stage timeout must not be below the tool timeout")
        self._tool_timeout_seconds = tool_timeout_seconds
        self._stage_timeout_seconds = stage_timeout_seconds
        self._metrics_sink = metrics_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache = cache or InMemoryResearchCache(clock=self._clock)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_concurrency = max_concurrency

    @classmethod
    def from_provider(
        cls,
        provider: ResearchProvider,
        *,
        search_timeout_seconds: float = 30.0,
        **kwargs,
    ) -> "ExternalResearchService":
        return cls(
            default_research_tools(provider, search_timeout_seconds=search_timeout_seconds),
            **kwargs,
        )

    @classmethod
    def from_providers(
        cls,
        research: ResearchProvider,
        llm: LLMProvider,
        *,
        search_timeout_seconds: float = 30.0,
        **kwargs,
    ) -> "ExternalResearchService":
        return cls(
            default_research_tools(research, llm, search_timeout_seconds=search_timeout_seconds),
            **kwargs,
        )

    async def run(self, payload: ExternalResearchInput) -> ExternalResearchResult:
        stage_started = monotonic()
        contract, context = validate_external_research_input(payload)
        researched_at = self._clock()
        if researched_at.tzinfo is None:
            raise ValueError("research clock must return a timezone-aware datetime")
        # Bounds concurrent provider calls, not tools. Holding a slot for a whole
        # tool let the three multi-dimension tools block the five cheap ones.
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute(tool: BaseResearchTool) -> ResearchReport:
            query = normalize_research_query(tool.build_query(context))
            key = research_cache_key(
                category=tool.category,
                query=query,
                locality=locality_cache_key(context),
                variant=tool.cache_variant,
            )
            cached = await self._cache_get(key)
            if (
                cached is not None
                and cached.category is tool.category
                and cached.cache_key == key
                and cached.expires_at > researched_at
            ):
                return cached.model_copy(update={"cached": True})
            report = await tool.research(
                context,
                researched_at=researched_at,
                ttl_seconds=self._cache_ttl_seconds,
                gate=semaphore,
            )
            if report.status is ResearchStatus.SUCCEEDED:
                await self._cache_set(key, report)
            else:
                # Caching an empty or failed report would hold the whole TTL and
                # be served straight back to the Supervisor's retry, turning a
                # momentary provider problem into an hour of empty research.
                logger.info(
                    "posts.research.not_cached",
                    extra={"category": tool.category.value, "status": report.status.value},
                )
            return report

        # A monotonic deadline, deliberately not derived from the injected
        # clock: the clock is a test seam and may be frozen, while the budget
        # has to track real elapsed time.
        deadline = asyncio.get_running_loop().time() + self._stage_timeout_seconds

        measurements: list[ResearchCategoryMetrics] = []
        # All eight categories are already in flight before the first response
        # returns, so a spent allowance cannot be short-circuited mid-run. What
        # it can do is change how the stage reports itself afterwards.
        quota_exhausted = False

        async def guarded(tool: BaseResearchTool) -> ResearchReport:
            nonlocal quota_exhausted
            remaining = deadline - asyncio.get_running_loop().time()
            budget = min(self._tool_timeout_seconds, remaining)
            started = monotonic()
            try:
                if budget <= 0:
                    raise TimeoutError("external research stage budget exhausted")
                # Bounding each tool rather than the whole gather keeps the
                # categories that already finished; cancelling the gather would
                # throw away work that was paid for.
                async with asyncio.timeout(budget):
                    report = await execute(tool)
            except Exception as exc:  # noqa: BLE001 - one category must not sink the rest
                quota_exhausted = quota_exhausted or isinstance(exc, ProviderQuotaError)
                error = safe_error_code(exc)
                logger.warning(
                    "posts.research.tool_failed",
                    extra={"category": tool.category.value, "error": error},
                )
                report = self._failed_report(
                    tool,
                    context,
                    researched_at=researched_at,
                    error=error,
                )
            measurements.append(
                ResearchCategoryMetrics.from_report(report, duration_ms=_elapsed_ms(started))
            )
            return report

        reports = await asyncio.gather(*(guarded(tool) for tool in self._tools))
        await self._record_metrics(
            ResearchStageMetrics(
                duration_ms=_elapsed_ms(stage_started),
                categories=tuple(
                    sorted(
                        measurements, key=lambda item: tuple(ResearchCategory).index(item.category)
                    )
                ),
            )
        )
        if all(report.status is ResearchStatus.FAILED for report in reports):
            # Total failure is an outage, not degraded research. Raising lets the
            # Supervisor retry the stage instead of persisting empty evidence.
            # A spent allowance is raised as itself: retrying will not recover
            # it, and the fix is to top up a plan rather than chase a defect.
            if quota_exhausted:
                raise ProviderQuotaError("external research provider allowance is exhausted")
            raise ProviderError("external research failed for every category")
        by_category = {report.category: report for report in reports}
        return ExternalResearchResult(
            market=by_category[ResearchCategory.MARKET],
            competitor=by_category[ResearchCategory.COMPETITOR],
            audience=by_category[ResearchCategory.AUDIENCE],
            social=by_category[ResearchCategory.SOCIAL],
            visual_reference=by_category[ResearchCategory.VISUAL_REFERENCE],
            trend=by_category[ResearchCategory.TREND],
            platform=by_category[ResearchCategory.PLATFORM],
            brand_product=by_category[ResearchCategory.BRAND_PRODUCT],
            researched_at=researched_at,
            contract_fingerprint=contract.fingerprint,
        )

    def _failed_report(
        self,
        tool: BaseResearchTool,
        context: ResearchContext,
        *,
        researched_at: datetime,
        error: str,
    ) -> ResearchReport:
        """A typed, evidence-free report standing in for a category that broke."""
        try:
            query = normalize_research_query(tool.build_query(context))
        except Exception:  # noqa: BLE001 - the query itself may be what failed
            query = f"{tool.category.value} research unavailable"
        return ResearchReport(
            category=tool.category,
            status=ResearchStatus.FAILED,
            query=query,
            provider="unavailable",
            confidence=ResearchConfidence.LOW,
            researched_at=researched_at,
            expires_at=researched_at + timedelta(seconds=self._cache_ttl_seconds),
            cache_key=research_cache_key(
                category=tool.category,
                query=query,
                locality=locality_cache_key(context),
                variant=tool.cache_variant,
            ),
            cached=False,
            error=error,
        )

    async def _record_metrics(self, metrics: ResearchStageMetrics) -> None:
        # Always logged, so the numbers exist even without a sink wired up.
        logger.info("posts.research.stage_completed", extra=metrics.as_metadata())
        if self._metrics_sink is None:
            return
        try:
            await self._metrics_sink.record(metrics)
        except Exception:  # noqa: BLE001 - measurement must never fail research
            logger.warning("posts.research.metrics_write_failed")

    async def _cache_get(self, key: str) -> ResearchReport | None:
        try:
            return await self._cache.get(key)
        except Exception:  # noqa: BLE001 - cache is an optimization
            logger.warning("posts.research.cache_read_failed", extra={"cache_key": key})
            return None

    async def _cache_set(self, key: str, report: ResearchReport) -> None:
        try:
            await self._cache.set(
                key,
                report,
                ttl_seconds=self._cache_ttl_seconds,
            )
        except Exception:  # noqa: BLE001 - successful research must survive cache failure
            logger.warning("posts.research.cache_write_failed", extra={"cache_key": key})


def validate_external_research_input(
    payload: ExternalResearchInput,
) -> tuple[PostSemanticContract, ResearchContext]:
    try:
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("external research requires a valid semantic contract") from exc
    audience: AudienceIntelligence = payload.audience
    if audience.contract_fingerprint != contract.fingerprint:
        raise ValueError("audience intelligence changed the semantic contract fingerprint")
    if audience.context.declared_audience != contract.audience:
        raise ValueError("audience intelligence changed the declared audience")
    if audience.context.market != contract.market:
        raise ValueError("audience intelligence changed the market")
    if audience.context.location != contract.location:
        raise ValueError("audience intelligence changed the location")
    if audience.context.platform != contract.platform:
        raise ValueError("audience intelligence changed the platform")
    return contract, ResearchContext(
        company=contract.company,
        brand=contract.brand,
        product=contract.product,
        primary_entity=contract.primary_entity,
        audience=contract.audience,
        target_segment=audience.target.segment,
        market=contract.market,
        location=contract.location,
        platform=contract.platform,
        language=contract.language,
        objective=contract.goal,
        required_facts=dict(contract.required_facts),
        contract_fingerprint=contract.fingerprint,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1_000))


__all__ = ["ExternalResearchService", "validate_external_research_input"]
