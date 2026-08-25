"""Operational visibility for the external research stage.

Every degradation in this stage is silent by design — a cache hit, a timed-out
category, a dimension that lost its search all still produce a valid result.
These tests pin that each of those is measured and reaches the trace timeline,
and that measurement never becomes a way for research to fail.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from test_external_research_service import _context, _payload, _ResearchProvider

from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    InMemoryExecutionTraceRecorder,
)
from app.modules.posts.orchestration.external_research import (
    ExternalResearchStageHandler,
    TraceResearchMetricsSink,
)
from app.modules.posts.providers import ProviderBundle, ResearchResponse
from app.modules.posts.tools.research import (
    EvidenceCoverageStatus,
    ExternalResearchService,
    InMemoryResearchCache,
    InMemoryResearchMetricsSink,
    ResearchCategory,
    ResearchCategoryMetrics,
    ResearchConfidence,
    ResearchStageMetrics,
    ResearchStatus,
    default_research_tools,
)

NOW = datetime(2026, 8, 25, 8, tzinfo=UTC)


def _service(provider, sink, **kwargs):
    return ExternalResearchService(
        default_research_tools(provider, search_timeout_seconds=kwargs.pop("search", 0.05)),
        cache=kwargs.pop("cache", None) or InMemoryResearchCache(clock=lambda: NOW),
        cache_ttl_seconds=600,
        max_concurrency=8,
        metrics_sink=sink,
        clock=lambda: NOW,
        **kwargs,
    )


def _metric(**overrides) -> ResearchCategoryMetrics:
    values = {
        "category": ResearchCategory.MARKET,
        "status": ResearchStatus.SUCCEEDED,
        "cached": False,
        "duration_ms": 10,
        "confidence": ResearchConfidence.MEDIUM,
        "source_count": 2,
        "visual_reference_count": 0,
        "degraded_dimension_count": 0,
        "coverage_status": None,
        "coverage_ratio": None,
        "mean_source_quality": None,
        "error": None,
    }
    values.update(overrides)
    return ResearchCategoryMetrics(**values)


# --------------------------------------------------------------------------
# What gets measured
# --------------------------------------------------------------------------


async def test_every_category_is_measured_once() -> None:
    sink = InMemoryResearchMetricsSink()
    await _service(_ResearchProvider(), sink).run(_payload())

    assert len(sink.recorded) == 1
    metrics = sink.recorded[0]
    assert [item.category for item in metrics.categories] == list(ResearchCategory)
    assert metrics.succeeded == 8
    assert metrics.total_sources > 0
    assert metrics.duration_ms >= 0


async def test_cache_hits_are_visible() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)
    first = InMemoryResearchMetricsSink()
    await _service(_ResearchProvider(), first, cache=cache).run(_payload())
    assert first.recorded[0].cache_hits == 0
    assert first.recorded[0].cache_hit_ratio == 0.0

    second = InMemoryResearchMetricsSink()
    await _service(_ResearchProvider(), second, cache=cache).run(_payload())
    assert second.recorded[0].cache_hits == 8
    assert second.recorded[0].cache_hit_ratio == 1.0


async def test_a_timed_out_category_is_counted_and_named() -> None:
    class _SlowTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                await asyncio.sleep(30)
            return await super().search(request)

    sink = InMemoryResearchMetricsSink()
    await _service(_SlowTrend(), sink, tool_timeout_seconds=0.2, stage_timeout_seconds=5.0).run(
        _payload()
    )

    metrics = sink.recorded[0]
    assert metrics.failed == 1
    assert metrics.timed_out == 1
    assert metrics.succeeded == 7
    trend = next(item for item in metrics.categories if item.category is ResearchCategory.TREND)
    assert trend.error == "TimeoutError"
    assert trend.source_count == 0


async def test_empty_research_is_distinguished_from_failure() -> None:
    class _Empty(_ResearchProvider):
        async def search(self, request):
            self.requests.append(request)
            return ResearchResponse(results=(), provider="p", query=request.query, answer=None)

    sink = InMemoryResearchMetricsSink()
    await _service(_Empty(), sink).run(_payload())

    metrics = sink.recorded[0]
    assert metrics.no_results == 8
    assert metrics.failed == 0
    assert metrics.total_sources == 0


async def test_degraded_dimensions_are_counted() -> None:
    class _SlowDimension(_ResearchProvider):
        async def search(self, request):
            if "customer reviews needs complaints" in request.query:
                await asyncio.sleep(30)
            return await super().search(request)

    sink = InMemoryResearchMetricsSink()
    await _service(_SlowDimension(), sink).run(_payload())

    metrics = sink.recorded[0]
    assert metrics.degraded_dimensions == 1
    assert metrics.succeeded == 8, "a degraded dimension is not a failed category"


async def test_the_slowest_live_category_is_identified() -> None:
    class _SlowSocial(_ResearchProvider):
        async def search(self, request):
            if "creative patterns" in request.query or "actual social posts" in request.query:
                await asyncio.sleep(0.05)
            return await super().search(request)

    sink = InMemoryResearchMetricsSink()
    await _service(_SlowSocial(), sink, search=5.0).run(_payload())

    assert sink.recorded[0].slowest_category is ResearchCategory.SOCIAL


def test_cached_categories_are_excluded_from_the_slowest() -> None:
    metrics = ResearchStageMetrics(
        duration_ms=100,
        categories=(
            _metric(category=ResearchCategory.MARKET, cached=True, duration_ms=999),
            _metric(category=ResearchCategory.TREND, cached=False, duration_ms=5),
        ),
    )
    assert metrics.slowest_category is ResearchCategory.TREND


def test_a_fully_cached_stage_has_no_slowest_category() -> None:
    metrics = ResearchStageMetrics(
        duration_ms=5,
        categories=(_metric(cached=True), _metric(category=ResearchCategory.TREND, cached=True)),
    )
    assert metrics.slowest_category is None
    assert metrics.cache_hit_ratio == 1.0


def test_incomplete_coverage_is_counted_only_where_coverage_exists() -> None:
    metrics = ResearchStageMetrics(
        duration_ms=10,
        categories=(
            _metric(coverage_status=EvidenceCoverageStatus.COMPLETE),
            _metric(
                category=ResearchCategory.COMPETITOR,
                coverage_status=EvidenceCoverageStatus.PARTIAL,
            ),
            _metric(
                category=ResearchCategory.SOCIAL,
                coverage_status=EvidenceCoverageStatus.INSUFFICIENT,
            ),
            # No structured analysis, so no coverage to judge.
            _metric(category=ResearchCategory.TREND, coverage_status=None),
        ),
    )
    assert metrics.incomplete_coverage == 2


# --------------------------------------------------------------------------
# Measurement must never break research
# --------------------------------------------------------------------------


async def test_a_broken_sink_does_not_fail_research() -> None:
    class _BrokenSink:
        async def record(self, metrics) -> None:
            raise ConnectionError("metrics backend unavailable")

    result = await _service(_ResearchProvider(), _BrokenSink()).run(_payload())

    assert result.market.status is ResearchStatus.SUCCEEDED


async def test_research_runs_without_any_sink() -> None:
    result = await _service(_ResearchProvider(), None).run(_payload())
    assert result.market.status is ResearchStatus.SUCCEEDED


# --------------------------------------------------------------------------
# Reaching the trace timeline
# --------------------------------------------------------------------------


def _providers(research) -> ProviderBundle:
    from test_external_research_service import _providers as build

    return build(research)


async def test_the_stage_handler_writes_measurements_to_the_trace_timeline() -> None:
    recorder = InMemoryExecutionTraceRecorder()
    handler = ExternalResearchStageHandler(
        _providers(_ResearchProvider()),
        trace_recorder=recorder,
    )

    await handler.execute(_context())

    names = [trace.name for trace in recorder.traces]
    for category in ResearchCategory:
        assert f"research.{category.value}" in names
    assert "research.stage" in names

    stage = next(trace for trace in recorder.traces if trace.name == "research.stage")
    assert stage.kind is ExecutionRunKind.GENERATION_STEP
    assert stage.status is ExecutionRunStatus.SUCCEEDED
    assert stage.metadata["succeeded"] == 8
    assert stage.metadata["cache_hits"] == 0
    assert stage.duration_ms >= 0

    market = next(trace for trace in recorder.traces if trace.name == "research.market")
    assert market.kind is ExecutionRunKind.TOOL
    assert market.metadata["category"] == "market"
    assert market.metadata["sources"] > 0


async def test_a_timed_out_category_is_traced_as_a_timeout() -> None:
    class _SlowTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                await asyncio.sleep(30)
            return await super().search(request)

    recorder = InMemoryExecutionTraceRecorder()
    handler = ExternalResearchStageHandler(
        _providers(_SlowTrend()),
        tool_timeout_seconds=0.2,
        stage_timeout_seconds=5.0,
        trace_recorder=recorder,
    )

    await handler.execute(_context())

    trend = next(trace for trace in recorder.traces if trace.name == "research.trend")
    assert trend.status is ExecutionRunStatus.TIMEOUT
    assert trend.error_code == "TimeoutError"


async def test_traces_carry_no_query_or_source_payload() -> None:
    recorder = InMemoryExecutionTraceRecorder()
    handler = ExternalResearchStageHandler(
        _providers(_ResearchProvider()),
        trace_recorder=recorder,
    )

    await handler.execute(_context())

    # Only the measurement traces. Provider-call traces share the "research."
    # prefix and carry non-reversible SHA-256 references by design.
    measured = {f"research.{category.value}" for category in ResearchCategory}
    measured.add("research.stage")
    metric_traces = [trace for trace in recorder.traces if trace.name in measured]
    assert len(metric_traces) == len(ResearchCategory) + 1
    for trace in metric_traces:
        rendered = str(trace.metadata)
        assert "http" not in rendered
        assert "Prishtina" not in rendered
        assert trace.input_reference is None
        assert trace.output_reference is None


async def test_no_recorder_means_no_metrics_plumbing() -> None:
    handler = ExternalResearchStageHandler(_providers(_ResearchProvider()))
    result = await handler.execute(_context())
    assert result.outputs


def test_trace_sink_is_usable_on_its_own() -> None:
    from uuid import uuid4

    from app.modules.posts.domain.contracts import InvocationContext

    recorder = InMemoryExecutionTraceRecorder()
    sink = TraceResearchMetricsSink(
        recorder,
        invocation=InvocationContext(
            correlation_id=uuid4(), post_id=uuid4(), generation_id=uuid4()
        ),
    )
    metrics = ResearchStageMetrics(duration_ms=42, categories=(_metric(),))

    asyncio.run(sink.record(metrics))

    assert [trace.name for trace in recorder.traces] == ["research.market", "research.stage"]


def test_a_stage_with_nothing_successful_is_traced_as_failed() -> None:
    from uuid import uuid4

    from app.modules.posts.domain.contracts import InvocationContext

    recorder = InMemoryExecutionTraceRecorder()
    sink = TraceResearchMetricsSink(
        recorder,
        invocation=InvocationContext(
            correlation_id=uuid4(), post_id=uuid4(), generation_id=uuid4()
        ),
    )
    metrics = ResearchStageMetrics(
        duration_ms=10,
        categories=(_metric(status=ResearchStatus.FAILED, error="ProviderError", source_count=0),),
    )

    asyncio.run(sink.record(metrics))

    stage = next(trace for trace in recorder.traces if trace.name == "research.stage")
    assert stage.status is ExecutionRunStatus.FAILED


@pytest.mark.parametrize("ratio", [0, 4, 8])
def test_cache_hit_ratio_is_a_fraction_of_categories(ratio: int) -> None:
    categories = tuple(
        _metric(category=category, cached=index < ratio)
        for index, category in enumerate(ResearchCategory)
    )
    metrics = ResearchStageMetrics(duration_ms=1, categories=categories)
    assert metrics.cache_hits == ratio
    assert metrics.cache_hit_ratio == round(ratio / 8, 4)
