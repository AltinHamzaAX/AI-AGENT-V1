"""Operational measurements for the external research stage.

Research is the most expensive stage in the workflow, and almost everything
that can go wrong with it degrades quietly by design: a poisoned cache still
returns reports, a timed-out category still produces a typed report, and a
filter that is too strict still produces coverage. Without measurement none of
that is visible.

These are operational numbers, not evidence. They travel through a sink to the
execution trace timeline rather than into workflow state, so downstream agents
keep reading evidence and nothing else.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from .schemas import (
    EvidenceCoverageStatus,
    ResearchCategory,
    ResearchConfidence,
    ResearchReport,
    ResearchStatus,
)


@dataclass(frozen=True, slots=True)
class ResearchCategoryMetrics:
    category: ResearchCategory
    status: ResearchStatus
    cached: bool
    duration_ms: int
    confidence: ResearchConfidence
    source_count: int
    visual_reference_count: int
    degraded_dimension_count: int
    coverage_status: EvidenceCoverageStatus | None
    coverage_ratio: float | None
    mean_source_quality: float | None
    error: str | None

    @classmethod
    def from_report(cls, report: ResearchReport, *, duration_ms: int) -> "ResearchCategoryMetrics":
        coverage = report.evidence_coverage
        return cls(
            category=report.category,
            status=report.status,
            cached=report.cached,
            duration_ms=duration_ms,
            confidence=report.confidence,
            source_count=len(report.sources),
            visual_reference_count=len(report.visual_references),
            degraded_dimension_count=len(report.degraded_dimensions),
            coverage_status=coverage.status if coverage is not None else None,
            coverage_ratio=coverage.coverage_ratio if coverage is not None else None,
            mean_source_quality=(coverage.mean_source_quality if coverage is not None else None),
            error=report.error,
        )

    def as_metadata(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "status": self.status.value,
            "cached": self.cached,
            "confidence": self.confidence.value,
            "sources": self.source_count,
            "visual_references": self.visual_reference_count,
            "degraded_dimensions": self.degraded_dimension_count,
            "coverage_status": (
                self.coverage_status.value if self.coverage_status is not None else None
            ),
            "coverage_ratio": self.coverage_ratio,
            "mean_source_quality": self.mean_source_quality,
        }


@dataclass(frozen=True, slots=True)
class ResearchStageMetrics:
    duration_ms: int
    categories: tuple[ResearchCategoryMetrics, ...]

    @property
    def cache_hits(self) -> int:
        return sum(1 for item in self.categories if item.cached)

    @property
    def cache_hit_ratio(self) -> float:
        if not self.categories:
            return 0.0
        return round(self.cache_hits / len(self.categories), 4)

    @property
    def succeeded(self) -> int:
        return self._count(ResearchStatus.SUCCEEDED)

    @property
    def failed(self) -> int:
        return self._count(ResearchStatus.FAILED)

    @property
    def no_results(self) -> int:
        return self._count(ResearchStatus.NO_RESULTS)

    @property
    def timed_out(self) -> int:
        return self._errors("TimeoutError")

    @property
    def quota_exhausted(self) -> int:
        """Categories lost to a spent plan allowance.

        Kept apart from other failures because the response is to top up a
        plan, not to investigate a defect, and no amount of retrying helps.
        """
        return self._errors("ProviderQuotaError")

    @property
    def rate_limited(self) -> int:
        """Categories lost to throttling, which retrying later does fix."""
        return self._errors("ProviderRateLimitError")

    @property
    def total_sources(self) -> int:
        return sum(item.source_count for item in self.categories)

    @property
    def degraded_dimensions(self) -> int:
        return sum(item.degraded_dimension_count for item in self.categories)

    @property
    def slowest_category(self) -> ResearchCategory | None:
        live = [item for item in self.categories if not item.cached]
        if not live:
            return None
        return max(live, key=lambda item: item.duration_ms).category

    @property
    def incomplete_coverage(self) -> int:
        return sum(
            1
            for item in self.categories
            if item.coverage_status is not None
            and item.coverage_status is not EvidenceCoverageStatus.COMPLETE
        )

    def as_metadata(self) -> dict[str, Any]:
        slowest = self.slowest_category
        return {
            "categories": len(self.categories),
            "succeeded": self.succeeded,
            "no_results": self.no_results,
            "failed": self.failed,
            "timed_out": self.timed_out,
            "quota_exhausted": self.quota_exhausted,
            "rate_limited": self.rate_limited,
            "cache_hits": self.cache_hits,
            "cache_hit_ratio": self.cache_hit_ratio,
            "total_sources": self.total_sources,
            "degraded_dimensions": self.degraded_dimensions,
            "incomplete_coverage": self.incomplete_coverage,
            "slowest_category": slowest.value if slowest is not None else None,
        }

    def _count(self, status: ResearchStatus) -> int:
        return sum(1 for item in self.categories if item.status is status)

    def _errors(self, code: str) -> int:
        return sum(1 for item in self.categories if item.error == code)


class ResearchMetricsSink(Protocol):
    async def record(self, metrics: ResearchStageMetrics) -> None: ...


class InMemoryResearchMetricsSink:
    """Deterministic sink for tests and local composition."""

    def __init__(self) -> None:
        self.recorded: list[ResearchStageMetrics] = []

    async def record(self, metrics: ResearchStageMetrics) -> None:
        self.recorded.append(metrics)


__all__ = [
    "InMemoryResearchMetricsSink",
    "ResearchCategoryMetrics",
    "ResearchMetricsSink",
    "ResearchStageMetrics",
]
