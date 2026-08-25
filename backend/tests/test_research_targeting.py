"""Provider targeting and non-English evidence handling.

Covers the two properties that decide whether external research is actually
good: whether the request narrows to the target market, and whether evidence
written in the market's own language survives to the report.
"""

import json
from datetime import UTC, datetime

import pytest

from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    SUPPORTED_COUNTRIES,
    AudienceResearchTool,
    BrandProductResearchTool,
    CompetitorResearchTool,
    LLMResearchAnalyzer,
    MarketResearchTool,
    PlatformResearchTool,
    ResearchCategory,
    ResearchConfidence,
    ResearchContext,
    SocialResearchTool,
    TrendResearchTool,
    VisualReferenceTool,
    platform_domains,
    research_cache_key,
    resolve_country,
)

RESEARCHED_AT = datetime(2026, 8, 25, 10, tzinfo=UTC)

# A real Albanian rental listing reads like this. Every dimension marker in the
# analyzer is English, so this text is the regression case that matters.
ALBANIAN_EXCERPT = (
    "Qiraja e veturave në Prishtinë fillon nga 35 euro në ditë, me marrje të "
    "veturës në aeroport 24 orë. Klientët presin çmime transparente dhe "
    "shërbim të shpejtë pa pritje të gjatë."
)
ALBANIAN_QUOTE = "fillon nga 35 euro në ditë"


def _context(**overrides) -> ResearchContext:
    values = {
        "company": "Promotiva Mobility",
        "brand": "Prishtina Drive",
        "product": "Airport car rental",
        "primary_entity": "Airport car rental",
        "audience": "Diaspora arriving in Kosovo",
        "target_segment": "Arrival convenience seekers",
        "market": "Kosovo",
        "location": "Prishtina airport",
        "platform": "Instagram",
        "language": "Albanian",
        "objective": "Increase airport pickup bookings",
        "required_facts": {"pickup": "24/7"},
        "contract_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return ResearchContext(**values)


class _RecordingProvider:
    def __init__(self, *, content: str = "Observed market demand and price evidence.") -> None:
        self.requests: list[ResearchRequest] = []
        self._content = content

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        return ResearchResponse(
            results=(
                ResearchResult(
                    title="Qira veturash Prishtinë",
                    url="https://rentacar-ks.example/cmimet",
                    content=self._content,
                    score=0.93,
                ),
            ),
            provider="test-search",
            query=request.query,
            answer=None,
        )


class _AlbanianAnalysisLLM:
    """Returns English observations citing verbatim Albanian evidence."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        prompt = request.messages[0].content
        insight = {
            "observation": "Local providers publish a daily price anchor near EUR 35.",
            "evidence": [
                {
                    "source_id": "S1",
                    "quote": ALBANIAN_QUOTE,
                    "translation": "starts from 35 euro per day",
                }
            ],
        }
        if "overused_patterns" in prompt:
            payload = {
                "messaging": [insight],
                "offers": [insight],
                "cta": [],
                "visual_language": [],
                "differentiation": [],
                "overused_patterns": [],
            }
        elif "platform_creative_patterns" in prompt:
            payload = {
                key: []
                for key in (
                    "platform_creative_patterns",
                    "text_density",
                    "cta",
                    "logo_placement",
                    "photography",
                    "graphic_systems",
                    "compositions",
                )
            }
            payload["platform_creative_patterns"] = [insight]
        else:
            payload = {
                "category": [insight],
                "market_expectations": [insight],
                "offers": [insight],
                "customer_expectations": [],
                "positioning_patterns": [],
                "opportunities": [],
            }
        return LLMResponse(text=json.dumps(payload), provider="test-llm", model="albanian")


# --------------------------------------------------------------------------
# Country resolution
# --------------------------------------------------------------------------


def test_kosovo_resolves_to_albania_because_the_provider_has_no_kosovo() -> None:
    assert "kosovo" not in SUPPORTED_COUNTRIES
    assert resolve_country(_context()) == "albania"
    assert resolve_country(_context(market="Kosovë", location=None)) == "albania"
    assert resolve_country(_context(market=None, location="Prishtina airport")) == "albania"


def test_country_resolves_from_market_location_and_aliases() -> None:
    assert resolve_country(_context(market="Albania")) == "albania"
    assert resolve_country(_context(market="Germany")) == "germany"
    assert resolve_country(_context(market="Swiss diaspora", location=None)) == "switzerland"
    assert resolve_country(_context(market="Tirana", location=None)) == "albania"
    # "macedonia" must not shadow the supported "north macedonia" value.
    assert resolve_country(_context(market="Macedonia", location=None)) == "north macedonia"
    assert resolve_country(_context(market="North Macedonia", location=None)) == "north macedonia"


def test_unresolvable_market_omits_the_country_rather_than_guessing() -> None:
    assert resolve_country(_context(market="Atlantis", location="Nowhere")) is None
    assert resolve_country(_context(market=None, location=None)) is None
    # The market is preferred over the location when both resolve.
    assert resolve_country(_context(market="Germany", location="Tirana")) == "germany"


def test_every_resolved_country_is_accepted_by_the_provider() -> None:
    markets = ["Kosovo", "Tirana", "Macedonia", "Swiss diaspora", "London", "New York", "Dubai"]
    for market in markets:
        resolved = resolve_country(_context(market=market, location=None))
        assert resolved is None or resolved in SUPPORTED_COUNTRIES


# --------------------------------------------------------------------------
# Request shaping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_type",
    [
        MarketResearchTool,
        CompetitorResearchTool,
        SocialResearchTool,
        AudienceResearchTool,
        VisualReferenceTool,
        BrandProductResearchTool,
    ],
)
async def test_market_facing_tools_geo_target_every_request(tool_type) -> None:
    provider = _RecordingProvider()
    await tool_type(provider).research(
        _context(),
        researched_at=RESEARCHED_AT,
        ttl_seconds=600,
    )

    assert provider.requests
    for request in provider.requests:
        assert request.country == "albania"
        assert request.topic == "general"


async def test_trend_tool_buys_recency_and_drops_geo_targeting() -> None:
    provider = _RecordingProvider()
    await TrendResearchTool(provider).research(
        _context(),
        researched_at=RESEARCHED_AT,
        ttl_seconds=600,
    )

    request = provider.requests[0]
    # Recency is ranked by freshness_score, not filtered for. Measured live,
    # filtering trends to news within a year returned one usable source in
    # five where the general index returned five.
    assert request.topic == "general"
    assert request.time_range is None
    # A trend is rarely local, and this engine judges market relevance itself
    # through brand, audience and objective fit.
    assert request.country is None


async def test_platform_tool_pins_official_documentation_domains() -> None:
    provider = _RecordingProvider()
    await PlatformResearchTool(provider).research(
        _context(),
        researched_at=RESEARCHED_AT,
        ttl_seconds=600,
    )

    request = provider.requests[0]
    assert "help.instagram.com" in request.include_domains
    assert "business.instagram.com" in request.include_domains
    # Platform specifications are global, not local to the client's market.
    assert request.country is None


async def test_unknown_platform_searches_the_open_web() -> None:
    provider = _RecordingProvider()
    await PlatformResearchTool(provider).research(
        _context(platform="Some Internal Portal"),
        researched_at=RESEARCHED_AT,
        ttl_seconds=600,
    )

    assert provider.requests[0].include_domains == ()
    assert platform_domains("Some Internal Portal") == ()


async def test_exclusions_are_opt_in_and_reach_the_provider() -> None:
    default = _RecordingProvider()
    await MarketResearchTool(default).research(
        _context(), researched_at=RESEARCHED_AT, ttl_seconds=600
    )
    assert default.requests[0].exclude_domains == ()

    tuned = _RecordingProvider()
    tool = MarketResearchTool(tuned, exclude_domains=("spam.example", "spam.example"))
    await tool.research(_context(), researched_at=RESEARCHED_AT, ttl_seconds=600)
    assert tuned.requests[0].exclude_domains == ("spam.example",)


def test_cache_variant_separates_differently_shaped_requests() -> None:
    provider = _RecordingProvider()
    market = MarketResearchTool(provider).cache_variant
    trend = TrendResearchTool(provider).cache_variant
    excluded = MarketResearchTool(provider, exclude_domains=("spam.example",)).cache_variant

    analyzed = MarketResearchTool(provider, analyzer=object()).cache_variant

    assert market != excluded, "a different domain filter is a different request"
    assert market != analyzed, "a raw report must never be served as an analyzed one"
    # Market and trend now shape their requests identically, which is safe
    # because the category is part of the cache key itself.
    assert market == trend
    assert research_cache_key(
        category=ResearchCategory.MARKET, query="q", locality="l", variant=market
    ) != research_cache_key(category=ResearchCategory.TREND, query="q", locality="l", variant=trend)


# --------------------------------------------------------------------------
# Non-English evidence
# --------------------------------------------------------------------------


async def test_albanian_evidence_survives_with_an_english_observation() -> None:
    provider = _RecordingProvider(content=ALBANIAN_EXCERPT)
    tool = MarketResearchTool(provider, analyzer=LLMResearchAnalyzer(_AlbanianAnalysisLLM()))

    report = await tool.research(_context(), researched_at=RESEARCHED_AT, ttl_seconds=600)

    assert report.analysis is not None
    assert report.evidence_coverage is not None
    # The regression: this was 0.0 while the sources were perfectly good.
    assert report.evidence_coverage.coverage_ratio > 0
    assert report.analysis.category

    insight = report.analysis.category[0]
    assert insight.observation.isascii(), "observations stay English for downstream agents"
    quote = insight.evidence[0]
    assert quote.quote == ALBANIAN_QUOTE, "quotes stay verbatim in the source language"
    assert quote.translation == "starts from 35 euro per day"
    assert insight.confidence is not ResearchConfidence.LOW


async def test_albanian_quote_must_still_be_verbatim() -> None:
    """Translation must not become a loophole around the grounding check."""

    class _ParaphrasingLLM(_AlbanianAnalysisLLM):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            response = await super().complete(request)
            payload = json.loads(response.text)
            payload["category"][0]["evidence"][0]["quote"] = "fillon nga 99 euro në ditë"
            return LLMResponse(text=json.dumps(payload), provider="x", model="y")

    tool = MarketResearchTool(
        _RecordingProvider(content=ALBANIAN_EXCERPT),
        analyzer=LLMResearchAnalyzer(_ParaphrasingLLM()),
    )

    report = await tool.research(_context(), researched_at=RESEARCHED_AT, ttl_seconds=600)

    # The paraphrase never reaches a report. Only the dimension that carried it
    # is lost, and the loss is stated rather than hidden: the untouched
    # dimensions beside it keep their verbatim Albanian evidence.
    assert report.evidence_coverage is not None
    assert not report.analysis.category
    assert report.analysis.market_expectations
    quotes = {
        quote.quote for insight in report.analysis.market_expectations for quote in insight.evidence
    }
    assert quotes == {ALBANIAN_QUOTE}
    assert "99 euro" not in json.dumps(report.model_dump(mode="json"))
    assert any(
        limitation.startswith("Unverifiable evidence was discarded for:")
        for limitation in report.evidence_coverage.limitations
    )


async def test_analysis_prompt_requires_verbatim_untranslated_quotes() -> None:
    llm = _AlbanianAnalysisLLM()
    tool = MarketResearchTool(
        _RecordingProvider(content=ALBANIAN_EXCERPT),
        analyzer=LLMResearchAnalyzer(llm),
    )

    await tool.research(_context(), researched_at=RESEARCHED_AT, ttl_seconds=600)

    prompt = llm.requests[0].messages[0].content
    assert "never translate them" in prompt
    assert "translation field" in prompt
    assert "first-class evidence" in prompt
