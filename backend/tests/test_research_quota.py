"""Telling a spent allowance apart from a defect.

A quota error, a throttle and a broken provider all used to arrive as the same
generic failure, so the trace timeline could not distinguish "top up the plan"
from "investigate a bug" from "wait and retry". These are different operational
answers and are now different types.
"""

from datetime import UTC, datetime

import httpx
import pytest
from test_external_research_service import _payload, _ResearchProvider
from test_research_timeouts import _context

from app.integrations.tavily import TavilyResearchProvider
from app.modules.posts.providers import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ResearchRequest,
)
from app.modules.posts.tools.research import (
    CompetitorResearchTool,
    ExternalResearchService,
    InMemoryResearchCache,
    InMemoryResearchMetricsSink,
    ResearchCategory,
    ResearchStatus,
    TrendResearchTool,
    default_research_tools,
)
from app.modules.posts.tools.research.metrics import ResearchStageMetrics

NOW = datetime(2026, 8, 25, 8, tzinfo=UTC)


def _tavily(status: int, body: str = "denied"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return handler


async def _search_with_status(status: int, body: str = "denied"):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_tavily(status, body))) as client:
        provider = TavilyResearchProvider(api_key="tvly-secret", client=client)
        await provider.search(ResearchRequest(query="market"))


# --------------------------------------------------------------------------
# Mapping HTTP status onto a typed failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [432, 402])
async def test_a_spent_allowance_is_its_own_error(status: int) -> None:
    """432 is what Tavily actually answered when the plan ran out."""
    with pytest.raises(ProviderQuotaError) as captured:
        await _search_with_status(status, "exceeds your plan's set usage limit")

    assert str(status) in str(captured.value)
    assert "tvly-secret" not in str(captured.value)
    assert "exceeds your plan" not in str(captured.value), "no provider body is echoed"


async def test_throttling_is_distinct_from_a_spent_allowance() -> None:
    with pytest.raises(ProviderRateLimitError) as captured:
        await _search_with_status(429)

    assert not isinstance(captured.value, ProviderQuotaError)
    assert isinstance(captured.value, ProviderError), "still a provider failure"


@pytest.mark.parametrize("status", [400, 401, 500, 503])
async def test_other_failures_stay_generic(status: int) -> None:
    with pytest.raises(ProviderError) as captured:
        await _search_with_status(status)

    assert not isinstance(captured.value, ProviderQuotaError | ProviderRateLimitError)


# --------------------------------------------------------------------------
# Carrying the reason up through the stage
# --------------------------------------------------------------------------


class _OutOfQuota(_ResearchProvider):
    async def search(self, request):
        self.requests.append(request)
        raise ProviderQuotaError("tavily usage allowance is exhausted (status 432)")


def _service(provider, sink=None):
    return ExternalResearchService(
        default_research_tools(provider),
        cache=InMemoryResearchCache(clock=lambda: NOW),
        cache_ttl_seconds=600,
        max_concurrency=8,
        metrics_sink=sink,
        clock=lambda: NOW,
    )


async def test_a_single_query_tool_propagates_the_quota_reason() -> None:
    with pytest.raises(ProviderQuotaError):
        await TrendResearchTool(_OutOfQuota()).research(
            _context(), researched_at=NOW, ttl_seconds=600
        )


async def test_a_structured_tool_does_not_flatten_the_quota_reason() -> None:
    """Every dimension failed, but not for a reason worth investigating."""
    with pytest.raises(ProviderQuotaError):
        await CompetitorResearchTool(_OutOfQuota()).research(
            _context(), researched_at=NOW, ttl_seconds=600
        )


async def test_the_stage_raises_quota_rather_than_a_generic_failure() -> None:
    with pytest.raises(ProviderQuotaError):
        await _service(_OutOfQuota()).run(_payload())


async def test_a_generic_outage_still_raises_a_generic_failure() -> None:
    class _Broken(_ResearchProvider):
        async def search(self, request):
            raise ProviderError("tavily request failed with status 500")

    with pytest.raises(ProviderError) as captured:
        await _service(_Broken()).run(_payload())
    assert not isinstance(captured.value, ProviderQuotaError)


# --------------------------------------------------------------------------
# Visible in the measurements
# --------------------------------------------------------------------------


async def test_quota_is_counted_apart_from_other_failures() -> None:
    class _QuotaOnTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise ProviderQuotaError("allowance exhausted (status 432)")
            return await super().search(request)

    sink = InMemoryResearchMetricsSink()
    await _service(_QuotaOnTrend(), sink).run(_payload())

    metrics = sink.recorded[0]
    assert metrics.quota_exhausted == 1
    assert metrics.rate_limited == 0
    assert metrics.timed_out == 0
    assert metrics.failed == 1
    assert metrics.succeeded == 7
    assert metrics.as_metadata()["quota_exhausted"] == 1


async def test_rate_limiting_is_counted_apart_from_quota() -> None:
    class _ThrottledTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise ProviderRateLimitError("rate limit reached (status 429)")
            return await super().search(request)

    sink = InMemoryResearchMetricsSink()
    await _service(_ThrottledTrend(), sink).run(_payload())

    metrics = sink.recorded[0]
    assert metrics.rate_limited == 1
    assert metrics.quota_exhausted == 0


def test_a_healthy_stage_reports_neither() -> None:
    metrics = ResearchStageMetrics(duration_ms=1, categories=())
    assert metrics.quota_exhausted == 0
    assert metrics.rate_limited == 0
    assert metrics.as_metadata()["rate_limited"] == 0


async def test_the_failed_category_still_reports_its_own_error_code() -> None:
    class _QuotaOnTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise ProviderQuotaError("allowance exhausted (status 432)")
            return await super().search(request)

    result = await _service(_QuotaOnTrend()).run(_payload())

    assert result.trend.status is ResearchStatus.FAILED
    assert result.trend.error == "ProviderQuotaError"
    for category in ResearchCategory:
        if category is not ResearchCategory.TREND:
            assert getattr(result, category.value).status is ResearchStatus.SUCCEEDED
