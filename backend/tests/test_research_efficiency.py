"""Cost and latency contract for external research.

Research is the most expensive stage in the workflow: eight tools, twenty-four
provider calls, and a payload that is written to workflow state, cached, and
pasted into every downstream prompt. These tests pin the two properties that
keep that affordable — searches overlap, and evidence is stored once.
"""

import asyncio
from datetime import UTC, datetime

from test_external_research_service import _payload

from app.modules.posts.providers import ResearchResponse, ResearchResult
from app.modules.posts.tools.research import (
    ExternalResearchService,
    InMemoryResearchCache,
    MarketResearchTool,
    ResearchConfidence,
    ResearchContext,
    default_research_tools,
)
from app.modules.posts.tools.research.quality import (
    confidence_for_quality,
    merge_source,
    source_from_result,
)
from app.modules.posts.tools.research.schemas import FINDING_EXTRACT_LIMIT
from app.modules.posts.tools.research.tools import _lead_extract

NOW = datetime(2026, 8, 25, 8, tzinfo=UTC)
LONG_EXCERPT = (
    "Airport car rental providers publish daily rates and pickup terms. "
    "Customers compare price clarity, availability and total cost. "
) * 25


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
        required_facts={"pickup": "24/7"},
        contract_fingerprint="a" * 64,
    )


class _ConcurrencyProvider:
    """Records how many searches are in flight at once."""

    def __init__(self, *, delay: float = 0.02, content: str = "Market demand evidence.") -> None:
        self._delay = delay
        self._content = content
        self.active = 0
        self.peak = 0
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self._delay)
            return ResearchResponse(
                results=(
                    ResearchResult(
                        title="Source",
                        url=f"https://ex.example/{len(self.requests)}",
                        content=self._content,
                        score=0.91,
                    ),
                ),
                provider="test",
                query=request.query,
                answer=None,
            )
        finally:
            self.active -= 1


# --------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------


async def test_dimension_searches_run_concurrently() -> None:
    provider = _ConcurrencyProvider()

    await MarketResearchTool(provider).research(_context(), researched_at=NOW, ttl_seconds=600)

    assert len(provider.requests) == 6
    assert provider.peak > 1, "dimension searches must overlap, not run one after another"


async def test_gate_bounds_provider_calls_rather_than_tools() -> None:
    provider = _ConcurrencyProvider()
    service = ExternalResearchService(
        default_research_tools(provider),
        cache=InMemoryResearchCache(clock=lambda: NOW),
        cache_ttl_seconds=600,
        max_concurrency=4,
        clock=lambda: NOW,
    )

    await service.run(_payload())

    assert len(provider.requests) == 24
    assert provider.peak == 4, "the gate must saturate to its limit and never exceed it"


async def test_a_cheap_tool_is_not_stuck_behind_a_structured_one() -> None:
    """With per-call gating, single-query tools finish early instead of waiting."""
    provider = _ConcurrencyProvider(delay=0.01)
    service = ExternalResearchService(
        default_research_tools(provider),
        cache=InMemoryResearchCache(clock=lambda: NOW),
        cache_ttl_seconds=600,
        max_concurrency=8,
        clock=lambda: NOW,
    )

    await service.run(_payload())

    # 24 calls at 8 in flight is 3 rounds; tool-level gating needed 8.
    assert provider.peak == 8


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


async def test_findings_quote_a_bounded_extract_not_the_whole_excerpt() -> None:
    provider = _ConcurrencyProvider(delay=0, content=LONG_EXCERPT)

    report = await MarketResearchTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    assert report.sources
    assert len(report.sources[0].excerpt) > FINDING_EXTRACT_LIMIT
    for finding in report.findings:
        assert len(finding.statement) <= FINDING_EXTRACT_LIMIT
        assert finding.statement != report.sources[0].excerpt


def test_lead_extract_cuts_on_a_sentence_boundary() -> None:
    short = "One sentence only."
    assert _lead_extract(short) == short
    assert _lead_extract("  spaced   out  text ") == "spaced out text"

    long_text = ("Sentence about market demand and price clarity. " * 20).strip()
    extract = _lead_extract(long_text)
    assert len(extract) <= FINDING_EXTRACT_LIMIT
    assert extract.endswith(".")

    unbroken = "x" * 900
    assert len(_lead_extract(unbroken)) <= FINDING_EXTRACT_LIMIT


# --------------------------------------------------------------------------
# Source confidence
# --------------------------------------------------------------------------


def test_source_confidence_tracks_the_composite_quality_score() -> None:
    assert confidence_for_quality(0.95) is ResearchConfidence.HIGH
    assert confidence_for_quality(0.80) is ResearchConfidence.HIGH
    assert confidence_for_quality(0.65) is ResearchConfidence.MEDIUM
    assert confidence_for_quality(0.50) is ResearchConfidence.MEDIUM
    assert confidence_for_quality(0.20) is ResearchConfidence.LOW


def test_merged_source_keeps_confidence_aligned_with_quality() -> None:
    context = _context()
    high = source_from_result(
        ResearchResult(
            title="Government guidance",
            url="https://transport.gov/kosovo-rental",
            content="Market demand evidence in Kosovo.",
            score=0.99,
        ),
        dimension="offers",
        context=context,
        researched_at=NOW,
    )
    low = source_from_result(
        ResearchResult(
            title="Blog recap",
            url="https://transport.gov/kosovo-rental",
            content="A shorter recap.",
            score=0.55,
        ),
        dimension="cta",
        context=context,
        researched_at=NOW,
    )

    merged = merge_source(low, high)

    assert merged.quality_score == max(low.quality_score, high.quality_score)
    assert merged.confidence is confidence_for_quality(merged.quality_score)
    assert set(merged.dimensions) == {"offers", "cta"}


def test_merging_sources_without_provider_scores_does_not_crash() -> None:
    from app.modules.posts.tools.research.schemas import ResearchSource

    first = ResearchSource(
        title="a", url="https://a.example", excerpt="x y z", retrieved_at=NOW, dimensions=["offers"]
    )
    second = ResearchSource(
        title="a", url="https://a.example", excerpt="q r s", retrieved_at=NOW, dimensions=["cta"]
    )

    merged = merge_source(first, second)

    assert merged.provider_score is None
    assert set(merged.dimensions) == {"offers", "cta"}


async def test_degraded_dimensions_default_to_empty() -> None:
    provider = _ConcurrencyProvider(delay=0)
    report = await MarketResearchTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )
    assert report.degraded_dimensions == []
