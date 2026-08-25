"""Time bounds for the external research stage.

A hung provider must not hold the generation job's budget. Three nested bounds
degrade in order: one dimension, then one category, then the stage — each
producing typed evidence about what was lost rather than hanging.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from test_external_research_service import _payload, _ResearchProvider

from app.core.config import Settings
from app.modules.posts.providers import (
    ProviderError,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    ExternalResearchService,
    InMemoryResearchCache,
    MarketResearchTool,
    ResearchCategory,
    ResearchContext,
    ResearchStatus,
    default_research_tools,
)

NOW = datetime(2026, 8, 25, 8, tzinfo=UTC)


def _context() -> ResearchContext:
    return ResearchContext(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Airport car rental",
        primary_entity="Airport car rental",
        audience="Diaspora arriving in Kosovo",
        target_segment="Arrival convenience seekers",
        market="Kosovo",
        location="Prishtina airport",
        platform="Instagram",
        language="Albanian",
        objective="Increase airport pickup bookings",
        required_facts={"pickup": "24/7"},
        contract_fingerprint="a" * 64,
    )


class _SlowProvider(_ResearchProvider):
    """Hangs on queries matching a marker, answers everything else."""

    def __init__(self, marker: str = "", *, delay: float = 30.0) -> None:
        super().__init__()
        self._marker = marker
        self._delay = delay

    async def search(self, request):
        if not self._marker or self._marker in request.query:
            await asyncio.sleep(self._delay)
        return await super().search(request)


def _service(provider, **kwargs):
    settings = {
        "cache": InMemoryResearchCache(clock=lambda: NOW),
        "cache_ttl_seconds": 600,
        "max_concurrency": 8,
        "clock": lambda: NOW,
        **kwargs,
    }
    tools = default_research_tools(
        provider, search_timeout_seconds=settings.pop("search_timeout_seconds", 0.05)
    )
    return ExternalResearchService(tools, **settings)


# --------------------------------------------------------------------------
# Search timeout: one dimension
# --------------------------------------------------------------------------


async def test_a_hung_dimension_degrades_only_that_angle() -> None:
    provider = _SlowProvider("customer reviews needs complaints")
    tool = MarketResearchTool(provider, search_timeout_seconds=0.05)

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert report.status is ResearchStatus.SUCCEEDED
    assert report.degraded_dimensions == ["customer_expectations"]
    assert report.sources, "the dimensions that answered still produce evidence"


async def test_a_hung_search_does_not_wait_for_the_provider() -> None:
    provider = _SlowProvider(delay=30.0)
    tool = MarketResearchTool(provider, search_timeout_seconds=0.05)

    started = asyncio.get_running_loop().time()
    # A hung provider surfaces as a timeout rather than a generic failure: the
    # stage metrics count timeouts apart from defects, and a category that runs
    # several searches must not lose that distinction by losing all of them.
    with pytest.raises(TimeoutError):
        await tool.research(_context(), researched_at=NOW, ttl_seconds=600)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, "the timeout must cut the call, not wait out the provider"


def test_search_timeout_is_validated() -> None:
    with pytest.raises(ValueError, match="search_timeout_seconds"):
        MarketResearchTool(_ResearchProvider(), search_timeout_seconds=0)


async def test_the_gate_wait_does_not_count_against_a_search() -> None:
    """A queued call must get its full budget once a slot frees up."""

    class _Steady:
        def __init__(self) -> None:
            self.requests = []

        async def search(self, request):
            self.requests.append(request)
            await asyncio.sleep(0.03)
            return ResearchResponse(
                results=(
                    ResearchResult(
                        title="t",
                        url=f"https://ex.example/{len(self.requests)}",
                        content="Market demand evidence.",
                        score=0.9,
                    ),
                ),
                provider="test",
                query=request.query,
                answer=None,
            )

    provider = _Steady()
    # One slot at a time, so later searches queue well past their own budget.
    service = _service(provider, max_concurrency=1, search_timeout_seconds=0.05)

    result = await service.run(_payload())

    assert len(provider.requests) == 31
    assert all(
        getattr(result, category.value).status is ResearchStatus.SUCCEEDED
        for category in ResearchCategory
    )


# --------------------------------------------------------------------------
# Tool timeout: one category
# --------------------------------------------------------------------------


async def test_a_hung_category_fails_without_sinking_the_rest() -> None:
    provider = _SlowProvider("trends", delay=30.0)
    service = _service(provider, tool_timeout_seconds=0.2, stage_timeout_seconds=5.0)

    result = await service.run(_payload())

    assert result.trend.status is ResearchStatus.FAILED
    assert result.trend.error == "TimeoutError"
    for category in ResearchCategory:
        if category is ResearchCategory.TREND:
            continue
        assert getattr(result, category.value).status is ResearchStatus.SUCCEEDED


async def test_a_timed_out_category_is_not_cached() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)
    slow = _SlowProvider("trends", delay=30.0)
    await _service(slow, cache=cache, tool_timeout_seconds=0.2, stage_timeout_seconds=5.0).run(
        _payload()
    )

    healthy = _ResearchProvider()
    result = await _service(healthy, cache=cache, tool_timeout_seconds=5.0).run(_payload())

    assert result.trend.status is ResearchStatus.SUCCEEDED
    assert result.trend.cached is False
    assert result.market.cached is True


# --------------------------------------------------------------------------
# Stage budget
# --------------------------------------------------------------------------


async def test_the_stage_budget_bounds_total_time() -> None:
    provider = _SlowProvider(delay=30.0)
    service = _service(provider, tool_timeout_seconds=0.2, stage_timeout_seconds=0.2)

    started = asyncio.get_running_loop().time()
    with pytest.raises(ProviderError):
        await service.run(_payload())
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, "the stage must not outlive its budget"


async def test_work_already_finished_survives_the_budget_running_out() -> None:
    """Bounding each tool, not the gather, keeps what was already paid for."""
    provider = _SlowProvider("trends", delay=30.0)
    service = _service(provider, tool_timeout_seconds=0.3, stage_timeout_seconds=0.3)

    result = await service.run(_payload())

    assert result.trend.status is ResearchStatus.FAILED
    assert result.market.status is ResearchStatus.SUCCEEDED
    assert result.market.sources


def test_service_timeouts_are_validated() -> None:
    provider = _ResearchProvider()
    with pytest.raises(ValueError, match="tool timeout must be positive"):
        ExternalResearchService(default_research_tools(provider), tool_timeout_seconds=0)
    with pytest.raises(ValueError, match="stage timeout must not be below"):
        ExternalResearchService(
            default_research_tools(provider),
            tool_timeout_seconds=120.0,
            stage_timeout_seconds=60.0,
        )


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    values = {
        "postgres_password": "test",
        "database_url": "sqlite+aiosqlite://",
        "redis_url": "redis://localhost:6379/0",
        "storage_provider": "mock",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "test",
        "s3_secret_key": "test",
        "s3_bucket": "test",
        **overrides,
    }
    return Settings(**values)


def test_research_timeout_defaults_fit_inside_the_job_budget() -> None:
    settings = _settings()
    assert settings.research_search_timeout_seconds <= settings.research_tool_timeout_seconds
    assert settings.research_tool_timeout_seconds <= settings.research_stage_timeout_seconds
    assert settings.research_stage_timeout_seconds < settings.generation_job_timeout_seconds


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"research_search_timeout_seconds": 200.0}, "RESEARCH_SEARCH_TIMEOUT_SECONDS"),
        ({"research_tool_timeout_seconds": 400.0}, "RESEARCH_TOOL_TIMEOUT_SECONDS"),
        ({"research_stage_timeout_seconds": 100_000.0}, "RESEARCH_STAGE_TIMEOUT_SECONDS"),
    ],
)
def test_inverted_timeout_configuration_is_rejected(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**overrides)
