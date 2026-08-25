"""Degradation behaviour of the external research stage.

Research calls eight independent tools across a flaky network. These tests fix
the boundary between "this category is degraded" and "this stage failed", and
make sure a momentary provider problem is never written into the cache as a
durable answer.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

# Fixture builders are shared with the sibling service tests rather than
# duplicated; tests/ has no conftest and each module owns its own fixtures.
from test_external_research_service import _payload, _ResearchProvider

from app.modules.posts.providers import (
    LLMResponse,
    ProviderError,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    ExternalResearchService,
    InMemoryResearchCache,
    ResearchCategory,
    ResearchConfidence,
    ResearchReport,
    ResearchStatus,
    default_research_tools,
)

NOW = datetime(2026, 8, 25, 8, tzinfo=UTC)
CATEGORIES = tuple(category.value for category in ResearchCategory)


def _service(provider, llm=None, *, cache=None):
    tools = default_research_tools(provider, llm) if llm else default_research_tools(provider)
    return ExternalResearchService(
        tools,
        cache=cache or InMemoryResearchCache(clock=lambda: NOW),
        cache_ttl_seconds=3_600,
        max_concurrency=4,
        clock=lambda: NOW,
    )


class _EmptyProvider(_ResearchProvider):
    async def search(self, request):
        self.requests.append(request)
        return ResearchResponse(results=(), provider="test", query=request.query, answer=None)


class _DeadProvider(_ResearchProvider):
    async def search(self, request):
        self.requests.append(request)
        raise RuntimeError("connection refused for api-key sk-live-secret")


# --------------------------------------------------------------------------
# One category must not sink the rest
# --------------------------------------------------------------------------


async def test_one_failing_tool_degrades_only_its_own_category() -> None:
    class _FlakyTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise RuntimeError("provider 503")
            return await super().search(request)

    provider = _FlakyTrend()
    result = await _service(provider).run(_payload())

    assert result.trend.status is ResearchStatus.FAILED
    for category in CATEGORIES:
        report = getattr(result, category)
        if category == "trend":
            continue
        assert report.status is ResearchStatus.SUCCEEDED, category
        assert report.sources, category


async def test_failed_report_is_evidence_free_and_leaks_no_provider_detail() -> None:
    class _OneBadTool(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise RuntimeError("connection refused for api-key sk-live-secret")
            return await super().search(request)

    result = await _service(_OneBadTool()).run(_payload())

    report = result.trend
    assert report.status is ResearchStatus.FAILED
    # Trend runs several searches, so a defect in all of them is reported as a
    # category failure rather than by whatever internal type happened to raise.
    assert report.error == "ProviderError"
    assert "sk-live-secret" not in report.model_dump_json()
    assert report.sources == []
    assert report.findings == []
    assert report.analysis is None
    assert report.confidence is ResearchConfidence.LOW
    assert report.category is ResearchCategory.TREND


async def test_analyzer_failure_degrades_only_the_analyzed_categories() -> None:
    class _BadLLM:
        async def complete(self, request):
            return LLMResponse(text="I cannot help with that.", provider="x", model="y")

    result = await _service(_ResearchProvider(), _BadLLM()).run(_payload())

    analyzed = {"market", "competitor", "social", "visual_reference", "trend", "platform"}
    for category in CATEGORIES:
        report = getattr(result, category)
        if category in analyzed:
            # Hallucinated or unparseable analysis must fail loudly rather than
            # quietly shipping a report that lost its analysis.
            assert report.status is ResearchStatus.FAILED, category
            assert report.error == "ProviderResponseError", category
        else:
            assert report.status is ResearchStatus.SUCCEEDED, category


async def test_no_search_is_abandoned_when_a_tool_fails_immediately() -> None:
    """gather must wait for siblings, not leave them running unobserved."""

    class _FailFast(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise RuntimeError("immediate failure")
            return await super().search(request)

    provider = _FailFast()
    result = await _service(provider).run(_payload())

    # 6 market + 6 competitor + 7 social + 4 visual reference + 2 platform
    # + audience + brand product, less the four trend searches that raised
    # before they were recorded.
    assert len(provider.requests) == 27
    completed = [c for c in CATEGORIES if getattr(result, c).status is ResearchStatus.SUCCEEDED]
    assert len(completed) == len(CATEGORIES) - 1
    assert result.trend.status is ResearchStatus.FAILED


async def test_one_failing_dimension_degrades_only_that_angle() -> None:
    class _FailOneDimension(_ResearchProvider):
        async def search(self, request):
            if "customer reviews needs complaints" in request.query:
                raise RuntimeError("provider 503")
            return await super().search(request)

    result = await _service(_FailOneDimension()).run(_payload())

    report = result.market
    assert report.status is ResearchStatus.SUCCEEDED
    assert report.sources, "the surviving dimensions still produce evidence"
    assert report.degraded_dimensions == ["customer_expectations"]
    assert report.error is None


async def test_all_dimensions_failing_fails_the_category() -> None:
    class _FailAllMarketDimensions(_ResearchProvider):
        async def search(self, request):
            if any(
                marker in request.query
                for marker in (
                    "category size",
                    "market standards",
                    "actual prices offers terms",
                    "customer reviews needs",
                    "official websites positioning",
                    "unmet needs service gaps",
                )
            ):
                raise RuntimeError("provider 503")
            return await super().search(request)

    result = await _service(_FailAllMarketDimensions()).run(_payload())

    assert result.market.status is ResearchStatus.FAILED
    assert result.market.error == "ProviderError"
    assert result.competitor.status is ResearchStatus.SUCCEEDED


async def test_total_provider_outage_raises_so_the_supervisor_retries() -> None:
    with pytest.raises(ProviderError):
        await _service(_DeadProvider()).run(_payload())


# --------------------------------------------------------------------------
# A momentary problem must not be cached as an answer
# --------------------------------------------------------------------------


async def test_empty_research_is_not_cached_and_recovers_on_retry() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)
    outage = _EmptyProvider()
    first = await _service(outage, cache=cache).run(_payload())
    assert first.market.status is ResearchStatus.NO_RESULTS

    healthy = _ResearchProvider()
    second = await _service(healthy, cache=cache).run(_payload())

    assert healthy.requests, "a retry must re-search rather than serve the empty report"
    assert second.market.status is ResearchStatus.SUCCEEDED
    assert second.market.sources
    assert second.market.cached is False


async def test_failed_research_is_not_cached_and_recovers_on_retry() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)

    class _FlakyTrend(_ResearchProvider):
        async def search(self, request):
            if "trends" in request.query:
                raise RuntimeError("provider 503")
            return await super().search(request)

    first = await _service(_FlakyTrend(), cache=cache).run(_payload())
    assert first.trend.status is ResearchStatus.FAILED

    healthy = _ResearchProvider()
    second = await _service(healthy, cache=cache).run(_payload())

    assert second.trend.status is ResearchStatus.SUCCEEDED
    assert second.trend.sources
    # The categories that did succeed are still served from cache.
    assert second.market.cached is True


async def test_successful_research_is_still_cached() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)
    provider = _ResearchProvider()
    await _service(provider, cache=cache).run(_payload())
    calls = len(provider.requests)

    second = await _service(provider, cache=cache).run(_payload())

    assert len(provider.requests) == calls, "a cached run must not call the provider"
    assert all(getattr(second, category).cached for category in CATEGORIES)


# --------------------------------------------------------------------------
# Malformed provider data
# --------------------------------------------------------------------------


async def test_unusable_url_is_skipped_not_fatal() -> None:
    class _MixedProvider(_ResearchProvider):
        async def search(self, request):
            return ResearchResponse(
                results=(
                    ResearchResult(
                        title="Relative link",
                        url="www.example.com/no-scheme",
                        content="Evidence about market demand and price.",
                        score=0.9,
                    ),
                    ResearchResult(
                        title="Usable source",
                        url="https://usable.example/evidence",
                        content="Evidence about market demand and price.",
                        score=0.88,
                    ),
                ),
                provider="test",
                query=request.query,
                answer=None,
            )

    result = await _service(_MixedProvider()).run(_payload())

    assert result.audience.status is ResearchStatus.SUCCEEDED
    urls = {str(source.url) for source in result.audience.sources}
    assert urls == {"https://usable.example/evidence"}


async def test_only_unusable_urls_is_no_results_not_a_crash() -> None:
    class _AllBad(_ResearchProvider):
        async def search(self, request):
            return ResearchResponse(
                results=(
                    ResearchResult(
                        title="Relative link",
                        url="not-a-url",
                        content="Some content about demand.",
                        score=0.9,
                    ),
                ),
                provider="test",
                query=request.query,
                answer=None,
            )

    result = await _service(_AllBad()).run(_payload())
    assert result.audience.status is ResearchStatus.NO_RESULTS
    assert result.audience.error is None


# --------------------------------------------------------------------------
# Report schema
# --------------------------------------------------------------------------


def _report(**overrides) -> dict:
    values = {
        "category": ResearchCategory.TREND,
        "status": ResearchStatus.FAILED,
        "query": "trend research",
        "provider": "unavailable",
        "confidence": ResearchConfidence.LOW,
        "researched_at": NOW,
        "expires_at": datetime(2026, 8, 25, 9, tzinfo=UTC),
        "cache_key": "b" * 64,
        "error": "ProviderResponseError",
    }
    values.update(overrides)
    return values


def test_failed_report_requires_a_safe_error_code() -> None:
    ResearchReport.model_validate(_report())
    with pytest.raises(ValidationError, match="failed research must record a safe error code"):
        ResearchReport.model_validate(_report(error=None))


def test_only_failed_reports_may_carry_an_error_code() -> None:
    with pytest.raises(ValidationError, match="only failed research may record an error code"):
        ResearchReport.model_validate(_report(status=ResearchStatus.NO_RESULTS))


def test_failed_report_cannot_smuggle_evidence() -> None:
    source = {
        "title": "t",
        "url": "https://a.example",
        "excerpt": "e",
        "retrieved_at": NOW,
    }
    with pytest.raises(ValidationError, match="cannot contain evidence"):
        ResearchReport.model_validate(_report(sources=[source]))
