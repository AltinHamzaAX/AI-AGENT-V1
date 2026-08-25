"""Evidence depth and cross-generation cache reuse.

Two properties decide whether research is worth what it costs: how much real
page text the analyzer gets to quote from, and whether a second post for the
same client in the same market has to pay for the same searches again.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
from test_external_research_service import _audience, _ResearchProvider

from app.integrations.tavily import TavilyResearchProvider
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    ANALYSIS_EXCERPT_LIMIT,
    AudienceResearchTool,
    CompetitorResearchTool,
    ExternalResearchInput,
    ExternalResearchService,
    InMemoryResearchCache,
    LLMResearchAnalyzer,
    MarketResearchTool,
    PlatformResearchTool,
    ResearchCategory,
    ResearchContext,
    SocialResearchTool,
    TrendResearchTool,
    default_research_tools,
    locality_cache_key,
    research_cache_key,
)
from app.modules.posts.tools.research.quality import source_from_result

NOW = datetime(2026, 8, 25, 10, tzinfo=UTC)
SNIPPET = "A short search snippet."
PAGE_BODY = (
    "Airport car rental providers publish daily rates, pickup terms and "
    "deposit rules on their own pages. Customers compare price clarity. "
) * 30


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
        "required_facts": {"pickup": "24/7"},
        "contract_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return ResearchContext(**values)


class _BodyProvider:
    def __init__(self, *, raw_content: str | None = PAGE_BODY) -> None:
        self.requests: list[ResearchRequest] = []
        self._raw = raw_content

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        return ResearchResponse(
            results=(
                ResearchResult(
                    title="Rental rates",
                    url=f"https://ex.example/{len(self.requests)}",
                    content=SNIPPET,
                    score=0.9,
                    raw_content=self._raw,
                ),
            ),
            provider="test",
            query=request.query,
            answer=None,
        )


# --------------------------------------------------------------------------
# Evidence depth
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_type", [MarketResearchTool, CompetitorResearchTool, SocialResearchTool]
)
async def test_structured_tools_ask_for_the_page_body(tool_type) -> None:
    provider = _BodyProvider()
    await tool_type(provider).research(_context(), researched_at=NOW, ttl_seconds=600)
    assert all(request.include_raw_content is True for request in provider.requests)


@pytest.mark.parametrize("tool_type", [AudienceResearchTool, TrendResearchTool])
async def test_snippet_only_tools_do_not_pay_for_page_bodies(tool_type) -> None:
    provider = _BodyProvider()
    await tool_type(provider).research(_context(), researched_at=NOW, ttl_seconds=600)
    assert all(request.include_raw_content is False for request in provider.requests)


def test_excerpt_prefers_the_page_body_over_the_snippet() -> None:
    source = source_from_result(
        ResearchResult(
            title="Rental rates",
            url="https://ex.example/a",
            content=SNIPPET,
            score=0.9,
            raw_content=PAGE_BODY,
        ),
        dimension="offers",
        context=_context(),
        researched_at=NOW,
    )

    assert source is not None
    assert len(source.excerpt) > len(SNIPPET) * 10
    assert source.excerpt.startswith("Airport car rental providers publish")


def test_excerpt_falls_back_to_the_snippet() -> None:
    for raw in (None, "", "   "):
        source = source_from_result(
            ResearchResult(
                title="Rental rates",
                url="https://ex.example/a",
                content=SNIPPET,
                score=0.9,
                raw_content=raw,
            ),
            dimension="offers",
            context=_context(),
            researched_at=NOW,
        )
        assert source is not None
        assert source.excerpt == SNIPPET


async def test_dimension_searches_cast_a_wider_net() -> None:
    provider = _BodyProvider()
    await MarketResearchTool(provider).research(_context(), researched_at=NOW, ttl_seconds=600)
    # Result count does not change what a Tavily search costs, so the old
    # hard-coded cap of three was leaving free evidence on the table.
    assert all(request.max_results == 5 for request in provider.requests)

    narrow = _BodyProvider()
    await MarketResearchTool(narrow, dimension_max_results=2).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )
    assert all(request.max_results == 2 for request in narrow.requests)


def test_dimension_result_count_is_validated() -> None:
    with pytest.raises(ValueError, match="dimension_max_results"):
        MarketResearchTool(_BodyProvider(), dimension_max_results=0)
    with pytest.raises(ValueError, match="dimension_max_results"):
        MarketResearchTool(_BodyProvider(), dimension_max_results=21)


class _CapturingLLM:
    """Records what the analyzer was shown and cites the front of it."""

    def __init__(self, seen: list[dict]) -> None:
        self._seen = seen

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = json.loads(request.messages[-1].content)
        self._seen.append(body)
        grounded = {
            "observation": "Providers publish a daily market price.",
            "evidence": [{"source_id": "S1", "quote": body["sources"][0]["excerpt"][:120]}],
        }
        payload = {
            "category": [grounded],
            "market_expectations": [],
            "offers": [],
            "customer_expectations": [],
            "positioning_patterns": [],
            "opportunities": [],
        }
        return LLMResponse(text=json.dumps(payload), provider="x", model="y")


async def test_analyzer_window_spends_its_whole_budget() -> None:
    seen: list[dict] = []
    tool = MarketResearchTool(_BodyProvider(), analyzer=LLMResearchAnalyzer(_CapturingLLM(seen)))

    await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    excerpt = seen[0]["sources"][0]["excerpt"]
    # Spans end on word boundaries, so the window lands just under its cap
    # rather than exactly on it. What matters is that it is not starved.
    assert ANALYSIS_EXCERPT_LIMIT * 0.9 <= len(excerpt) <= ANALYSIS_EXCERPT_LIMIT


async def test_analyzer_window_reaches_evidence_the_front_of_the_page_buries() -> None:
    """The window is ranked, not sliced off the top.

    This is the failure that made a real run unusable: an aggregator page
    opened with a language picker, so the only quotable text in the model's
    view was a booking-form fragment, while the prices sat further down.
    """
    buried = "Daily rates at Prishtina airport start from EUR 29 per day in November."
    body = ("Generic corporate boilerplate about mobility and travel worldwide. " * 50) + buried
    seen: list[dict] = []
    tool = MarketResearchTool(
        _BodyProvider(raw_content=body),
        analyzer=LLMResearchAnalyzer(_CapturingLLM(seen)),
    )

    await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    excerpt = seen[0]["sources"][0]["excerpt"]
    assert len(excerpt) <= ANALYSIS_EXCERPT_LIMIT
    assert buried in excerpt, "a prefix window would never have reached the priced sentence"


async def test_tavily_requests_markdown_bodies_and_parses_them() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "query": "rates",
                "results": [
                    {
                        "title": "Rental rates",
                        "url": "https://ex.example/a",
                        "content": SNIPPET,
                        "score": 0.9,
                        "raw_content": "## Rates\n\nFrom EUR 35 per day.",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-test", client=client)
        response = await provider.search(ResearchRequest(query="rates", include_raw_content=True))

    assert captured["include_raw_content"] == "markdown"
    assert response.results[0].raw_content == "## Rates\n\nFrom EUR 35 per day."


async def test_tavily_omits_bodies_when_not_requested() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "query": "rates",
                "results": [
                    {
                        "title": "t",
                        "url": "https://ex.example/a",
                        "content": SNIPPET,
                        "score": 0.9,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-test", client=client)
        response = await provider.search(ResearchRequest(query="rates"))

    assert captured["include_raw_content"] is False
    assert response.results[0].raw_content is None


# --------------------------------------------------------------------------
# Cache reuse
# --------------------------------------------------------------------------


def _key(context: ResearchContext, tool) -> str:
    return research_cache_key(
        category=tool.category,
        query=tool.build_query(context),
        locality=locality_cache_key(context),
        variant=tool.cache_variant,
    )


def test_cache_key_ignores_contract_fields_that_never_reach_a_search() -> None:
    tool = MarketResearchTool(_BodyProvider())
    first = _key(_context(contract_fingerprint="a" * 64), tool)
    second = _key(_context(contract_fingerprint="b" * 64), tool)

    assert first == second, "a different generation of the same question must reuse the answer"


def test_cache_key_still_separates_places() -> None:
    tool = MarketResearchTool(_BodyProvider())
    kosovo = _key(_context(), tool)
    germany = _key(_context(market="Germany", location="Berlin"), tool)
    # Same market, different location: locality_score and country targeting
    # differ, so the reports genuinely differ.
    other_city = _key(_context(location="Peja"), tool)

    assert kosovo != germany
    assert kosovo != other_city


def test_platform_research_is_shared_across_clients_in_a_market() -> None:
    tool = PlatformResearchTool(_BodyProvider())
    one = _key(_context(company="A", brand="A Rentals", contract_fingerprint="a" * 64), tool)
    two = _key(_context(company="B", brand="B Rentals", contract_fingerprint="b" * 64), tool)

    assert one == two


def test_brand_research_stays_separated_by_brand() -> None:
    tool = MarketResearchTool(_BodyProvider())
    one = _key(_context(primary_entity="Airport car rental"), tool)
    two = _key(_context(primary_entity="Wedding photography"), tool)

    assert one != two


def _payload(*, goal: str, offer: str) -> ExternalResearchInput:
    contract = PostSemanticContract.create(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Airport car rental",
        primary_entity="Airport car rental",
        goal=goal,
        audience="Diaspora arriving in Kosovo",
        market="Kosovo",
        location="Prishtina airport",
        offer=offer,
        cta_intent="Book now",
        platform="Instagram",
        language="Albanian",
        required_facts={"pickup availability": "24/7 airport pickup"},
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=[],
        constraints=["Do not replace the product or logo"],
    )
    return ExternalResearchInput(
        semantic_contract=contract.to_dict(),
        audience=_audience(contract),
    )


async def test_a_second_post_for_the_same_client_reuses_the_research() -> None:
    cache = InMemoryResearchCache(clock=lambda: NOW)

    def service(provider):
        return ExternalResearchService(
            default_research_tools(provider),
            cache=cache,
            cache_ttl_seconds=3_600,
            max_concurrency=8,
            clock=lambda: NOW,
        )

    first_provider = _ResearchProvider()
    await service(first_provider).run(_payload(goal="Drive bookings", offer="From EUR 35/day"))
    assert first_provider.requests

    # A different post: new goal and new offer, so a different contract and a
    # different fingerprint — but the same questions of the open web.
    second_provider = _ResearchProvider()
    result = await service(second_provider).run(
        _payload(goal="Grow winter demand", offer="From EUR 29/day")
    )

    assert second_provider.requests == [], "the second generation must not re-search"
    assert all(getattr(result, category.value).cached for category in ResearchCategory), (
        "every category should be served from cache"
    )
