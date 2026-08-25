from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from hashlib import sha256

from app.modules.posts.providers import ResearchProvider, ResearchRequest

from .schemas import (
    ResearchCategory,
    ResearchConfidence,
    ResearchContext,
    ResearchFinding,
    ResearchReport,
    ResearchSource,
    ResearchStatus,
)


class BaseResearchTool(ABC):
    category: ResearchCategory

    def __init__(
        self,
        provider: ResearchProvider,
        *,
        max_results: int = 5,
        search_depth: str = "advanced",
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("research max_results must be between 1 and 20")
        self._provider = provider
        self._max_results = max_results
        self._search_depth = search_depth

    @abstractmethod
    def build_query(self, context: ResearchContext) -> str: ...

    async def research(
        self,
        context: ResearchContext,
        *,
        researched_at: datetime,
        ttl_seconds: int,
    ) -> ResearchReport:
        query = normalize_research_query(self.build_query(context))
        response = await self._provider.search(
            ResearchRequest(
                query=query,
                max_results=self._max_results,
                search_depth=self._search_depth,
            )
        )
        sources: list[ResearchSource] = []
        findings: list[ResearchFinding] = []
        seen_urls: set[str] = set()
        for result in response.results:
            url = result.url.strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = " ".join(result.title.split())
            content = " ".join(result.content.split())
            if not title or not content:
                continue
            confidence = _confidence(result.score)
            sources.append(
                ResearchSource(
                    title=title,
                    url=url,
                    excerpt=content[:4_000],
                    provider_score=result.score,
                    retrieved_at=researched_at,
                )
            )
            findings.append(
                ResearchFinding(
                    statement=content[:4_000],
                    source_url=url,
                    confidence=confidence,
                )
            )
        report_confidence = _report_confidence(findings)
        cache_key = research_cache_key(
            category=self.category,
            query=query,
            contract_fingerprint=context.contract_fingerprint,
            variant=self.cache_variant,
        )
        return ResearchReport(
            category=self.category,
            status=(ResearchStatus.SUCCEEDED if sources else ResearchStatus.NO_RESULTS),
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
            researched_at=researched_at,
            expires_at=researched_at + timedelta(seconds=ttl_seconds),
            cache_key=cache_key,
            cached=False,
        )

    @property
    def cache_variant(self) -> str:
        return f"{self._search_depth}:{self._max_results}"


class MarketResearchTool(BaseResearchTool):
    category = ResearchCategory.MARKET

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} market demand customer behavior and category dynamics "
            f"in {context.market or context.location or 'the target market'} current evidence"
        )


class CompetitorResearchTool(BaseResearchTool):
    category = ResearchCategory.COMPETITOR

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} competitors alternatives and differentiators "
            f"in {context.market or context.location or 'the target market'}"
        )


class AudienceResearchTool(BaseResearchTool):
    category = ResearchCategory.AUDIENCE

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.audience} {context.target_segment} needs pain points objections "
            f"purchase behavior for {context.primary_entity}"
        )


class SocialResearchTool(BaseResearchTool):
    category = ResearchCategory.SOCIAL

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.platform} social content behavior engagement patterns for "
            f"{context.audience} and {context.primary_entity}"
        )


class VisualReferenceTool(BaseResearchTool):
    category = ResearchCategory.VISUAL_REFERENCE

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"{context.primary_entity} advertising visual references photography composition "
            f"in {context.market or 'the target market'} {context.platform}"
        )


class TrendResearchTool(BaseResearchTool):
    category = ResearchCategory.TREND

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"current {context.primary_entity} consumer and marketing trends "
            f"{context.market or context.location or ''}"
        )


class PlatformResearchTool(BaseResearchTool):
    category = ResearchCategory.PLATFORM

    def build_query(self, context: ResearchContext) -> str:
        return (
            f"official {context.platform} content specifications recommendations and current "
            f"best practices for business posts"
        )


class BrandProductResearchTool(BaseResearchTool):
    category = ResearchCategory.BRAND_PRODUCT

    def build_query(self, context: ResearchContext) -> str:
        subject = context.brand or context.product or context.primary_entity
        facts = " ".join(f"{key} {value}" for key, value in context.required_facts.items())
        return f"{subject} verified brand product information {facts}".strip()


def default_research_tools(provider: ResearchProvider) -> tuple[BaseResearchTool, ...]:
    return (
        MarketResearchTool(provider),
        CompetitorResearchTool(provider),
        AudienceResearchTool(provider),
        SocialResearchTool(provider),
        VisualReferenceTool(provider),
        TrendResearchTool(provider),
        PlatformResearchTool(provider),
        BrandProductResearchTool(provider),
    )


def research_cache_key(
    *,
    category: ResearchCategory,
    query: str,
    contract_fingerprint: str,
    variant: str,
) -> str:
    canonical = (
        f"ticket18:v1:{variant}:{category.value}:{contract_fingerprint}:{query.casefold()}"
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def normalize_research_query(value: str) -> str:
    query = " ".join(value.split())[:2_000].strip()
    if not query:
        raise ValueError("research query cannot be blank")
    return query


def _confidence(score: float | None) -> ResearchConfidence:
    if score is not None and score >= 0.8:
        return ResearchConfidence.HIGH
    if score is not None and score >= 0.5:
        return ResearchConfidence.MEDIUM
    return ResearchConfidence.LOW


def _report_confidence(findings: list[ResearchFinding]) -> ResearchConfidence:
    if not findings:
        return ResearchConfidence.LOW
    levels = [finding.confidence for finding in findings]
    if levels.count(ResearchConfidence.HIGH) >= 2:
        return ResearchConfidence.HIGH
    if ResearchConfidence.HIGH in levels or ResearchConfidence.MEDIUM in levels:
        return ResearchConfidence.MEDIUM
    return ResearchConfidence.LOW


__all__ = [
    "AudienceResearchTool",
    "BaseResearchTool",
    "BrandProductResearchTool",
    "CompetitorResearchTool",
    "MarketResearchTool",
    "PlatformResearchTool",
    "SocialResearchTool",
    "TrendResearchTool",
    "VisualReferenceTool",
    "default_research_tools",
    "normalize_research_query",
    "research_cache_key",
]
