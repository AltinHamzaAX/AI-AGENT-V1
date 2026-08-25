import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import nullcontext
from datetime import datetime, timedelta
from hashlib import sha256

from pydantic import ValidationError

from app.modules.posts.providers import (
    LLMProvider,
    ProviderError,
    ProviderQuotaError,
    ResearchImage,
    ResearchProvider,
    ResearchRequest,
)

from .analysis import LLMResearchAnalyzer, ResearchAnalyzer
from .quality import merge_source, source_from_result
from .schemas import (
    FINDING_EXTRACT_LIMIT,
    ResearchCategory,
    ResearchConfidence,
    ResearchContext,
    ResearchFinding,
    ResearchReport,
    ResearchSource,
    ResearchStatus,
    ResearchVisualReference,
)
from .targeting import platform_domains, resolve_country

logger = logging.getLogger(__name__)

#: A no-op stand-in when no shared concurrency gate is supplied, so a tool can
#: be exercised on its own without the service around it.
ResearchGate = asyncio.Semaphore

#: Cache namespace for research reports. Bump on any change to request shaping
#: or report structure so previously cached reports are not served.
RESEARCH_CACHE_SCHEMA = "research:v5"


class BaseResearchTool(ABC):
    category: ResearchCategory

    #: Provider search index. "news" trades geo-targeting for recency.
    topic: str = "general"
    #: Hard recency window, or None to rank on freshness instead of filtering.
    time_range: str | None = None
    #: Whether the declared market should narrow the search to a country.
    geo_targeted: bool = True
    #: Whether the provider should return observed images alongside text.
    include_images: bool = False
    #: Whether to ask for the extracted page body. Search snippets are a few
    #: hundred characters, which is too thin to ground quoted evidence in.
    include_raw_content: bool = False

    def __init__(
        self,
        provider: ResearchProvider,
        *,
        max_results: int = 5,
        search_depth: str = "advanced",
        exclude_domains: tuple[str, ...] = (),
        search_timeout_seconds: float = 30.0,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("research max_results must be between 1 and 20")
        if search_timeout_seconds <= 0:
            raise ValueError("research search_timeout_seconds must be positive")
        self._provider = provider
        self._max_results = max_results
        self._search_depth = search_depth
        self._exclude_domains = tuple(dict.fromkeys(exclude_domains))
        self._search_timeout_seconds = search_timeout_seconds

    async def _search(self, request: ResearchRequest, *, gate: "ResearchGate | None"):
        """One provider call, bounded in time.

        The gate is acquired outside the timeout so that waiting for a free
        slot never counts against the call's own budget.
        """
        async with gate or nullcontext():
            async with asyncio.timeout(self._search_timeout_seconds):
                return await self._provider.search(request)

    @abstractmethod
    def build_query(self, context: ResearchContext) -> str: ...

    def include_domains(self, context: ResearchContext) -> tuple[str, ...]:
        """Domains to pin. Empty means search the open web."""
        return ()

    def exclude_domains(self, context: ResearchContext) -> tuple[str, ...]:
        """Domains to drop at query time.

        Empty by default on purpose: low-authority sources are demoted by
        ResearchSource.authority_score rather than excluded, which keeps recall
        when nothing better exists. This is a tuning hook, not a default policy.
        """
        return self._exclude_domains

    def build_request(
        self,
        context: ResearchContext,
        *,
        query: str,
        max_results: int,
    ) -> ResearchRequest:
        # country is only honoured alongside topic="general".
        country = (
            resolve_country(context) if self.geo_targeted and self.topic == "general" else None
        )
        return ResearchRequest(
            query=query,
            max_results=max_results,
            search_depth=self._search_depth,
            include_domains=self.include_domains(context),
            exclude_domains=self.exclude_domains(context),
            topic=self.topic,
            time_range=self.time_range,
            country=country,
            include_images=self.include_images,
            include_raw_content=self.include_raw_content,
        )

    async def research(
        self,
        context: ResearchContext,
        *,
        researched_at: datetime,
        ttl_seconds: int,
        gate: ResearchGate | None = None,
    ) -> ResearchReport:
        query = normalize_research_query(self.build_query(context))
        response = await self._search(
            self.build_request(context, query=query, max_results=self._max_results),
            gate=gate,
        )
        sources: list[ResearchSource] = []
        for result in response.results:
            source = source_from_result(
                result,
                dimension=self.category.value,
                context=context,
                researched_at=researched_at,
            )
            if source is not None and all(source.url != item.url for item in sources):
                sources.append(source)
        visual_references = _visual_references(response.images, researched_at=researched_at)
        findings = _findings(sources)
        report_confidence = _report_confidence(sources)
        cache_key = research_cache_key(
            category=self.category,
            query=query,
            locality=locality_cache_key(context),
            variant=self.cache_variant,
        )
        return ResearchReport(
            category=self.category,
            status=(
                ResearchStatus.SUCCEEDED
                if (sources or visual_references)
                else ResearchStatus.NO_RESULTS
            ),
            query=query,
            provider=response.provider,
            provider_summary=(
                " ".join(response.answer.split())[:8_000]
                if isinstance(response.answer, str) and response.answer.strip()
                else None
            ),
            confidence=report_confidence,
            findings=findings,
            sources=sources,
            visual_references=visual_references,
            researched_at=researched_at,
            expires_at=researched_at + timedelta(seconds=ttl_seconds),
            cache_key=cache_key,
            cached=False,
        )

    @property
    def cache_variant(self) -> str:
        # Context-derived targeting (country, platform domains) follows from
        # the query and locality already in the cache key; only tool-level
        # configuration needs to appear here.
        excluded = ",".join(self._exclude_domains) or "none"
        return (
            f"{self._search_depth}:{self._max_results}"
            f":{self.topic}:{self.time_range or 'any'}:{excluded}"
            f":{'images' if self.include_images else 'text'}"
            f":{'body' if self.include_raw_content else 'snippet'}"
        )


class StructuredResearchTool(BaseResearchTool):
    """Adds grounded structured analysis to a source-collection tool."""

    # These tools must quote verbatim evidence, so they need real page bodies
    # rather than snippets. Result count does not change what a search costs,
    # so a wider net per dimension is close to free and gives the quality
    # ranking something to choose between.
    include_raw_content = True

    def __init__(
        self,
        provider: ResearchProvider,
        *,
        analyzer: ResearchAnalyzer | None = None,
        max_results: int = 5,
        dimension_max_results: int = 5,
        search_depth: str = "advanced",
        exclude_domains: tuple[str, ...] = (),
        search_timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            provider,
            max_results=max_results,
            search_depth=search_depth,
            exclude_domains=exclude_domains,
            search_timeout_seconds=search_timeout_seconds,
        )
        if not 1 <= dimension_max_results <= 20:
            raise ValueError("research dimension_max_results must be between 1 and 20")
        self._dimension_max_results = dimension_max_results
        self._analyzer = analyzer

    @abstractmethod
    def build_dimension_queries(self, context: ResearchContext) -> dict[str, str]: ...

    async def research(
        self,
        context: ResearchContext,
        *,
        researched_at: datetime,
        ttl_seconds: int,
        gate: ResearchGate | None = None,
    ) -> ResearchReport:
        canonical_query = normalize_research_query(self.build_query(context))
        dimension_queries = self.build_dimension_queries(context)

        async def search(dimension: str, raw_query: str):
            query = normalize_research_query(raw_query)
            return await self._search(
                self.build_request(
                    context,
                    query=query,
                    max_results=self._dimension_max_results,
                ),
                gate=gate,
            )

        # Dimensions are independent searches; running them one after another
        # made this tool the critical path of the whole research stage.
        responses = await asyncio.gather(
            *(search(dimension, query) for dimension, query in dimension_queries.items()),
            return_exceptions=True,
        )

        sources_by_url: dict[str, ResearchSource] = {}
        providers: set[str] = set()
        summaries: list[str] = []
        degraded: list[str] = []
        failures: list[BaseException] = []
        # gather preserves input order, so merging stays deterministic.
        for dimension, response in zip(dimension_queries, responses, strict=True):
            if isinstance(response, BaseException):
                # One dimension failing loses that angle, not the category.
                logger.warning(
                    "posts.research.dimension_failed",
                    extra={
                        "category": self.category.value,
                        "dimension": dimension,
                        "error": type(response).__name__,
                    },
                )
                degraded.append(dimension)
                failures.append(response)
                continue
            providers.add(response.provider)
            if isinstance(response.answer, str) and response.answer.strip():
                summary = " ".join(response.answer.split())
                if summary not in summaries:
                    summaries.append(summary)
            for result in response.results:
                source = source_from_result(
                    result,
                    dimension=dimension,
                    context=context,
                    researched_at=researched_at,
                )
                if source is None:
                    continue
                key = str(source.url)
                existing = sources_by_url.get(key)
                sources_by_url[key] = (
                    merge_source(existing, source) if existing is not None else source
                )
        if len(degraded) == len(dimension_queries):
            # Keep the reason rather than flattening it: a spent allowance is
            # not a defect to investigate, and the metrics rely on the type.
            if any(isinstance(failure, ProviderQuotaError) for failure in failures):
                raise ProviderQuotaError(
                    f"{self.category.value} research stopped: provider allowance is exhausted"
                )
            raise ProviderError(f"every {self.category.value} research dimension failed")
        sources = sorted(
            sources_by_url.values(),
            key=lambda source: source.quality_score,
            reverse=True,
        )[:20]
        report = ResearchReport(
            category=self.category,
            status=(ResearchStatus.SUCCEEDED if sources else ResearchStatus.NO_RESULTS),
            query=canonical_query,
            provider=", ".join(sorted(providers)) or "research_provider",
            provider_summary=" ".join(summaries)[:8_000] or None,
            confidence=_report_confidence(sources),
            findings=_findings(sources),
            sources=sources,
            researched_at=researched_at,
            expires_at=researched_at + timedelta(seconds=ttl_seconds),
            cache_key=research_cache_key(
                category=self.category,
                query=canonical_query,
                locality=locality_cache_key(context),
                variant=self.cache_variant,
            ),
            cached=False,
            degraded_dimensions=degraded,
        )
        if self._analyzer is None or report.status is ResearchStatus.NO_RESULTS:
            return report
        return await self._analyzer.analyze(report=report, context=context)

    @property
    def cache_variant(self) -> str:
        suffix = "analyzed" if self._analyzer is not None else "raw"
        return f"{super().cache_variant}:{suffix}"


class MarketResearchTool(StructuredResearchTool):
    category = ResearchCategory.MARKET

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} category market expectations current offers customer "
            f"expectations positioning patterns opportunities in "
            f"{context.market or context.location or 'the target market'} current evidence"
        )

    def build_dimension_queries(self, context: ResearchContext) -> dict[str, str]:
        subject = context.primary_entity
        place = context.market or context.location or "target market"
        return {
            "category": f"{subject} category size demand dynamics {place} current 2026",
            "market_expectations": (
                f"{subject} market standards service expectations {place} current"
            ),
            "offers": f"{subject} actual prices offers terms official providers {place}",
            "customer_expectations": (
                f"{subject} customer reviews needs complaints expectations {place}"
            ),
            "positioning_patterns": (
                f"{subject} providers {place} official websites positioning promises"
            ),
            "opportunities": (f"{subject} unmet needs service gaps customer complaints {place}"),
        }


class CompetitorResearchTool(StructuredResearchTool):
    category = ResearchCategory.COMPETITOR

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} competitor messaging offers CTA visual language "
            f"differentiation overused advertising patterns "
            f"in {context.market or context.location or 'the target market'}"
        )

    def build_dimension_queries(self, context: ResearchContext) -> dict[str, str]:
        subject = context.primary_entity
        place = context.market or context.location or "target market"
        return {
            "messaging": f"{subject} competitors {place} official website messaging claims",
            "offers": f"{subject} competitors {place} actual offers prices rental terms",
            "cta": f"{subject} competitors {place} official booking CTA reserve call action",
            "visual_language": (
                f"{subject} competitors {place} Instagram Facebook advertising visual style"
            ),
            "differentiation": (
                f"{subject} competitors {place} unique services differentiators official"
            ),
            "overused_patterns": (
                f"{subject} competitors {place} repeated advertising claims common patterns"
            ),
        }


class AudienceResearchTool(BaseResearchTool):
    category = ResearchCategory.AUDIENCE

    def build_query(self, context: ResearchContext) -> str:
        # Phrased as something a real page matches. Concatenating the field
        # names ("needs pain points objections purchase behavior") described
        # what we wanted rather than what a page says, and scored so poorly
        # against the index that the whole category came back empty.
        place = context.market or context.location or "the target market"
        return (
            f"{context.primary_entity} {place} customer reviews complaints "
            f"and what {context.audience} look for before booking"
        )


class SocialResearchTool(StructuredResearchTool):
    category = ResearchCategory.SOCIAL

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.platform} creative patterns text density CTA logo placement "
            f"photography graphic systems composition for {context.audience} "
            f"and {context.primary_entity}"
        )

    def build_dimension_queries(self, context: ResearchContext) -> dict[str, str]:
        subject = context.primary_entity
        platform = context.platform
        place = context.market or context.location or "target market"
        base = f"{platform} {subject} {place} actual social posts"
        return {
            "platform_creative_patterns": f"{base} recurring creative formats",
            "text_density": f"{base} carousel image text density examples",
            "cta": f"{base} captions booking call to action examples",
            "logo_placement": f"{base} logo placement branded creative examples",
            "photography": f"{base} photography product vehicle image style",
            "graphic_systems": f"{base} colors typography graphic templates",
            "compositions": f"{base} image composition layout examples",
        }


class VisualReferenceTool(BaseResearchTool):
    category = ResearchCategory.VISUAL_REFERENCE

    # Without this the tool could only ever find pages *about* visuals, never a
    # visual reference, which is the one thing this category exists to collect.
    include_images = True

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} advertising visual references photography composition "
            f"in {context.market or 'the target market'} {context.platform}"
        )


class TrendResearchTool(BaseResearchTool):
    category = ResearchCategory.TREND

    # Recency is the whole point of trend evidence, so this tool filters on it
    # rather than asking for "current" in prose and hoping the index agrees.
    # The provider ignores country on the news index, so geo-targeting is off
    # and the market stays in the query text instead.
    topic = "news"
    time_range = "year"
    geo_targeted = False

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"current {context.primary_entity} consumer and marketing trends "
            f"{context.market or context.location or ''}"
        )


class PlatformResearchTool(BaseResearchTool):
    category = ResearchCategory.PLATFORM

    # Platform specifications are global, and the authoritative source is the
    # platform's own documentation rather than whoever ranks for it locally.
    geo_targeted = False

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"official {context.platform} content specifications recommendations and current "
            f"best practices for business posts"
        )

    def include_domains(self, context: ResearchContext) -> tuple[str, ...]:
        return platform_domains(context.platform)


class BrandProductResearchTool(BaseResearchTool):
    category = ResearchCategory.BRAND_PRODUCT

    def build_query(self, context: ResearchContext) -> str:
        subject = context.brand or context.product or context.primary_entity
        facts = " ".join(f"{key} {value}" for key, value in context.required_facts.items())
        return f"{subject} verified brand product information {facts}".strip()


def default_research_tools(
    provider: ResearchProvider,
    llm: LLMProvider | None = None,
    *,
    search_timeout_seconds: float = 30.0,
) -> tuple[BaseResearchTool, ...]:
    analyzer = LLMResearchAnalyzer(llm) if llm is not None else None
    timeout = {"search_timeout_seconds": search_timeout_seconds}
    return (
        MarketResearchTool(provider, analyzer=analyzer, **timeout),
        CompetitorResearchTool(provider, analyzer=analyzer, **timeout),
        AudienceResearchTool(provider, **timeout),
        SocialResearchTool(provider, analyzer=analyzer, **timeout),
        VisualReferenceTool(provider, **timeout),
        TrendResearchTool(provider, **timeout),
        PlatformResearchTool(provider, **timeout),
        BrandProductResearchTool(provider, **timeout),
    )


def locality_cache_key(context: ResearchContext) -> str:
    """The context that changes a report beyond its query.

    Everything else in the semantic contract — goal, offer, CTA intent,
    forbidden claims — reaches a report only through the query text. Market and
    location do not: they drive `locality_score` and country targeting, so two
    identical queries in different places are genuinely different reports.
    """
    parts = [
        " ".join((value or "").split()).casefold() for value in (context.market, context.location)
    ]
    return "|".join(parts)


def research_cache_key(
    *,
    category: ResearchCategory,
    query: str,
    locality: str,
    variant: str,
) -> str:
    # Deliberately not keyed on the contract fingerprint. The fingerprint
    # covers fields that never reach a search, so it made every report private
    # to one generation and the cache hit rate effectively zero. Two contracts
    # that produce the same query in the same place asked the same question of
    # the open web, and should share the answer.
    #
    # Bump RESEARCH_CACHE_SCHEMA whenever request shaping or report structure
    # changes, so reports built by an older pipeline are never served.
    canonical = f"{RESEARCH_CACHE_SCHEMA}:{variant}:{category.value}:{locality}:{query.casefold()}"
    return sha256(canonical.encode("utf-8")).hexdigest()


def normalize_research_query(value: str) -> str:
    query = " ".join(value.split())[:2_000].strip()
    if not query:
        raise ValueError("research query cannot be blank")
    return query


def _findings(sources: list[ResearchSource]) -> list[ResearchFinding]:
    return [
        ResearchFinding(
            statement=_lead_extract(source.excerpt),
            source_url=source.url,
            confidence=source.confidence,
        )
        for source in sources
    ]


def _visual_references(
    images: tuple[ResearchImage, ...],
    *,
    researched_at: datetime,
) -> list[ResearchVisualReference]:
    references: list[ResearchVisualReference] = []
    seen: set[str] = set()
    for image in images:
        if image.url in seen:
            continue
        seen.add(image.url)
        try:
            references.append(
                ResearchVisualReference(
                    url=image.url,
                    description=image.description,
                    retrieved_at=researched_at,
                )
            )
        except ValidationError:
            # Same policy as text sources: an unusable entry is skipped, not
            # a reason to lose the category.
            logger.warning("posts.research.unusable_image")
        if len(references) == 20:
            break
    return references


def _lead_extract(excerpt: str) -> str:
    """The opening of an excerpt, cut on a sentence boundary where there is one.

    The full excerpt stays on the source. A finding points at it.
    """
    text = " ".join(excerpt.split())
    if len(text) <= FINDING_EXTRACT_LIMIT:
        return text
    window = text[:FINDING_EXTRACT_LIMIT]
    for terminator in (". ", "! ", "? "):
        cut = window.rfind(terminator)
        if cut >= FINDING_EXTRACT_LIMIT // 2:
            return window[: cut + 1]
    cut = window.rfind(" ")
    return window[:cut] if cut >= FINDING_EXTRACT_LIMIT // 2 else window


def _report_confidence(sources: list[ResearchSource]) -> ResearchConfidence:
    if not sources:
        return ResearchConfidence.LOW
    levels = [source.confidence for source in sources]
    if levels.count(ResearchConfidence.HIGH) >= 2:
        return ResearchConfidence.HIGH
    if ResearchConfidence.HIGH in levels or ResearchConfidence.MEDIUM in levels:
        return ResearchConfidence.MEDIUM
    return ResearchConfidence.LOW


__all__ = [
    "RESEARCH_CACHE_SCHEMA",
    "AudienceResearchTool",
    "BaseResearchTool",
    "BrandProductResearchTool",
    "CompetitorResearchTool",
    "MarketResearchTool",
    "PlatformResearchTool",
    "SocialResearchTool",
    "StructuredResearchTool",
    "TrendResearchTool",
    "VisualReferenceTool",
    "default_research_tools",
    "locality_cache_key",
    "normalize_research_query",
    "research_cache_key",
]
