import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.domain.semantic_contract import PostSemanticContract

from .cache import InMemoryResearchCache, ResearchCache
from .schemas import (
    ExternalResearchInput,
    ExternalResearchResult,
    ResearchCategory,
    ResearchContext,
    ResearchReport,
)
from .tools import (
    BaseResearchTool,
    default_research_tools,
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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tools = tuple(tools)
        categories = [tool.category for tool in self._tools]
        if set(categories) != set(ResearchCategory) or len(categories) != len(
            ResearchCategory
        ):
            raise ValueError("external research requires exactly one tool per category")
        if not 1 <= max_concurrency <= len(ResearchCategory):
            raise ValueError("research max_concurrency must be between 1 and 8")
        if cache_ttl_seconds <= 0:
            raise ValueError("research cache TTL must be positive")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache = cache or InMemoryResearchCache(clock=self._clock)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_concurrency = max_concurrency

    @classmethod
    def from_provider(cls, provider, **kwargs) -> "ExternalResearchService":
        return cls(default_research_tools(provider), **kwargs)

    async def run(self, payload: ExternalResearchInput) -> ExternalResearchResult:
        contract, context = validate_external_research_input(payload)
        researched_at = self._clock()
        if researched_at.tzinfo is None:
            raise ValueError("research clock must return a timezone-aware datetime")
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute(tool: BaseResearchTool) -> ResearchReport:
            query = normalize_research_query(tool.build_query(context))
            key = research_cache_key(
                category=tool.category,
                query=query,
                contract_fingerprint=contract.fingerprint,
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
            async with semaphore:
                report = await tool.research(
                    context,
                    researched_at=researched_at,
                    ttl_seconds=self._cache_ttl_seconds,
                )
            await self._cache_set(key, report)
            return report

        reports = await asyncio.gather(*(execute(tool) for tool in self._tools))
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
        required_facts=dict(contract.required_facts),
        contract_fingerprint=contract.fingerprint,
    )


__all__ = ["ExternalResearchService", "validate_external_research_input"]
