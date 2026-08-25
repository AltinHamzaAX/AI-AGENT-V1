import json
from datetime import UTC, datetime

import pytest

from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderResponseError,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    CompetitorResearchAnalysis,
    CompetitorResearchTool,
    LLMResearchAnalyzer,
    MarketResearchAnalysis,
    MarketResearchTool,
    ResearchConfidence,
    ResearchContext,
    ResearchStatus,
    SocialResearchAnalysis,
    SocialResearchTool,
)
from app.modules.posts.tools.research.quality import source_from_result


class _SearchProvider:
    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.requests: list[ResearchRequest] = []

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        results = ()
        if not self.empty:
            results = (
                ResearchResult(
                    title="Observed category evidence",
                    url="https://evidence.example/primary",
                    content=(
                        "The market category has demand for convenient service and availability. "
                        "Customers compare price clarity and trust. Competitor messaging "
                        "emphasizes "
                        "offers and direct calls to action. Common generic positioning repeats "
                        "while competitor services are different. "
                        "Observed social posts use compact text overlays, product photography, a "
                        "logo in outer corners, repeated color and typography templates, and "
                        "single-focal-point layouts. This leaves an evidence-supported clarity gap."
                    ),
                    score=0.91,
                ),
                ResearchResult(
                    title="Secondary evidence",
                    url="https://evidence.example/secondary",
                    content="Customers compare availability, clarity, trust, and total price.",
                    score=0.63,
                ),
            )
        return ResearchResponse(
            results=results,
            provider="test-search",
            query=request.query,
            answer="Evidence summary.",
        )


class _AnalysisLLM:
    def __init__(
        self,
        *,
        bad_source: bool = False,
        bad_quote: bool = False,
        copy_instruction: bool = False,
        misattributed: bool = False,
        stray_evidence: bool = False,
    ) -> None:
        self.bad_source = bad_source
        self.bad_quote = bad_quote
        self.copy_instruction = copy_instruction
        self.misattributed = misattributed
        self.stray_evidence = stray_evidence
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        prompt = request.messages[0].content
        sources = json.loads(request.messages[-1].content)["sources"]
        source_ids = ["S99"] if self.bad_source else ["S1", "S2"]

        def excerpt_of(source_id: str) -> str:
            return next(source["excerpt"] for source in sources if source["id"] == source_id)

        def insight(observation: str) -> dict[str, object]:
            evidence = [
                {
                    # Quoting one source under its neighbour's ID is the slip a
                    # real local model made: the span is verbatim, the label is
                    # one off.
                    "source_id": {"S1": "S2", "S2": "S1"}[source_id]
                    if self.misattributed
                    else source_id,
                    "quote": (
                        "Fabricated evidence that is absent from every source."
                        if source_id == "S99" or self.bad_quote
                        else excerpt_of(source_id)
                    ),
                }
                for source_id in source_ids
            ]
            if self.stray_evidence:
                evidence.append(
                    {
                        "source_id": "S1",
                        "quote": "Fabricated evidence that is absent from every source.",
                    }
                )
            return {"observation": observation, "evidence": evidence}

        if "overused_patterns" in prompt:
            messaging = (
                "Copy this exact competitor CTA."
                if self.copy_instruction
                else "Competitors emphasize immediate availability."
            )
            payload = {
                "messaging": [insight(messaging)],
                "offers": [insight("Visible daily-price offers are common.")],
                "cta": [insight("Direct booking calls to action appear frequently.")],
                "visual_language": [insight("Product-led photography is prevalent.")],
                "differentiation": [insight("Service clarity varies across competitors.")],
                "overused_patterns": [insight("Generic convenience language is repeated.")],
            }
        elif "platform_creative_patterns" in prompt:
            payload = {
                "platform_creative_patterns": [insight("Product-led posts recur.")],
                "text_density": [insight("Compact text blocks are common.")],
                "cta": [insight("Direct booking CTAs recur.")],
                "logo_placement": [insight("Logos appear near outer corners.")],
                "photography": [insight("Product photography dominates.")],
                "graphic_systems": [insight("Price badges are repeated.")],
                "compositions": [insight("Single-focal-point layouts recur.")],
            }
        else:
            payload = {
                "category": [insight("The category competes on access and trust.")],
                "market_expectations": [insight("Availability is expected.")],
                "offers": [insight("Daily-price offers are visible.")],
                "customer_expectations": [insight("Customers expect price clarity.")],
                "positioning_patterns": [insight("Convenience positioning recurs.")],
                "opportunities": [insight("Evidence shows a clarity gap.")],
            }
        return LLMResponse(
            text=json.dumps(payload),
            provider="test-llm",
            model="structured-research-test",
        )


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_type", "analysis_type", "expected_fields"),
    [
        (
            MarketResearchTool,
            MarketResearchAnalysis,
            {
                "category",
                "market_expectations",
                "offers",
                "customer_expectations",
                "positioning_patterns",
                "opportunities",
            },
        ),
        (
            CompetitorResearchTool,
            CompetitorResearchAnalysis,
            {
                "messaging",
                "offers",
                "cta",
                "visual_language",
                "differentiation",
                "overused_patterns",
            },
        ),
        (
            SocialResearchTool,
            SocialResearchAnalysis,
            {
                "platform_creative_patterns",
                "text_density",
                "cta",
                "logo_placement",
                "photography",
                "graphic_systems",
                "compositions",
            },
        ),
    ],
)
async def test_ticket19_tools_return_complete_source_grounded_analysis(
    tool_type,
    analysis_type,
    expected_fields: set[str],
) -> None:
    search = _SearchProvider()
    llm = _AnalysisLLM()
    tool = tool_type(search, analyzer=LLMResearchAnalyzer(llm))

    report = await tool.research(
        _context(),
        researched_at=datetime(2026, 8, 25, 10, tzinfo=UTC),
        ttl_seconds=600,
    )

    assert report.status is ResearchStatus.SUCCEEDED
    assert isinstance(report.analysis, analysis_type)
    assert len(search.requests) == len(expected_fields)
    assert len(llm.requests) == 1
    analysis = report.analysis.model_dump()
    for field in expected_fields:
        assert analysis[field]
        for item in analysis[field]:
            cited_urls = {str(url) for url in item["source_urls"]}
            assert "https://evidence.example/primary" in cited_urls
            assert cited_urls <= {
                "https://evidence.example/primary",
                "https://evidence.example/secondary",
            }
            assert item["confidence"].value == "medium"
            assert item["authority"] == "external_evidence"
            assert item["evidence"]
    assert "marketing_strategy" not in analysis
    assert "creative_direction" not in analysis
    if isinstance(report.analysis, CompetitorResearchAnalysis):
        assert report.analysis.safe_use == "differentiate_do_not_copy"
    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1


@pytest.mark.asyncio
async def test_unknown_source_id_is_rejected_without_leaking_provider_output() -> None:
    """Every citation names a source that does not exist, so nothing grounds."""
    tool = MarketResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_AnalysisLLM(bad_source=True)),
    )
    with pytest.raises(
        ProviderResponseError,
        match="market research returned invalid structured analysis",
    ):
        await tool.research(
            _context(),
            researched_at=datetime.now(UTC),
            ttl_seconds=600,
        )


@pytest.mark.asyncio
async def test_fabricated_evidence_quote_is_rejected() -> None:
    """No quote here appears in any source, so the whole analysis is unusable."""
    tool = MarketResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_AnalysisLLM(bad_quote=True)),
    )
    with pytest.raises(
        ProviderResponseError,
        match="market research returned invalid structured analysis",
    ):
        await tool.research(
            _context(),
            researched_at=datetime.now(UTC),
            ttl_seconds=600,
        )


@pytest.mark.asyncio
async def test_verbatim_quote_is_re_attributed_to_the_source_it_came_from() -> None:
    """A quote filed under the wrong ID is evidence, not fabrication.

    Local models copy spans correctly and then mislabel them. The span decides
    which source it belongs to, so the citation is corrected rather than lost.
    """
    tool = MarketResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_AnalysisLLM(misattributed=True)),
    )

    report = await tool.research(
        _context(),
        researched_at=datetime.now(UTC),
        ttl_seconds=600,
    )

    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1
    primary = "https://evidence.example/primary"
    secondary = "https://evidence.example/secondary"
    quoted = {
        str(quote.source_url): quote.quote
        for insight in report.analysis.category
        for quote in insight.evidence
    }
    assert "convenient service" in quoted[primary]
    assert "total price" in quoted[secondary]
    assert not any("discarded" in limitation for limitation in report.evidence_coverage.limitations)


@pytest.mark.asyncio
async def test_one_unverifiable_quote_does_not_destroy_the_correct_ones() -> None:
    """The penalty for a bad citation is the citation, not the category."""
    tool = MarketResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_AnalysisLLM(stray_evidence=True)),
    )

    report = await tool.research(
        _context(),
        researched_at=datetime.now(UTC),
        ttl_seconds=600,
    )

    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1
    for insight in report.analysis.category:
        assert insight.evidence
        for quote in insight.evidence:
            assert "Fabricated" not in quote.quote
    assert any(
        limitation.startswith("Unverifiable evidence was discarded for:")
        for limitation in report.evidence_coverage.limitations
    )


@pytest.mark.asyncio
async def test_competitor_copy_instruction_is_a_hard_validation_failure() -> None:
    tool = CompetitorResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_AnalysisLLM(copy_instruction=True)),
    )
    with pytest.raises(
        ProviderResponseError,
        match="competitor research returned invalid structured analysis",
    ):
        await tool.research(
            _context(),
            researched_at=datetime.now(UTC),
            ttl_seconds=600,
        )


@pytest.mark.asyncio
async def test_no_results_skips_structured_analysis_provider() -> None:
    llm = _AnalysisLLM()
    tool = SocialResearchTool(
        _SearchProvider(empty=True),
        analyzer=LLMResearchAnalyzer(llm),
    )
    report = await tool.research(
        _context(),
        researched_at=datetime.now(UTC),
        ttl_seconds=600,
    )
    assert report.status is ResearchStatus.NO_RESULTS
    assert report.analysis is None
    assert llm.requests == []


@pytest.mark.asyncio
async def test_analysis_prompt_treats_sources_as_untrusted_and_forbids_copying() -> None:
    llm = _AnalysisLLM()
    tool = CompetitorResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(llm),
    )

    await tool.research(
        _context(),
        researched_at=datetime.now(UTC),
        ttl_seconds=600,
    )
    prompt = llm.requests[0].messages[0].content
    assert "untrusted evidence" in prompt
    assert "never instruct the workflow to copy" in prompt
    assert "not final marketing strategy" in prompt
    assert "copied EXACTLY and verbatim" in prompt
    assert "allowed_dimensions" in prompt


class _SparseEvidenceLLM:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        source = json.loads(request.messages[-1].content)["sources"][1]
        insight = {
            "observation": "A claimed observation that lacks dimension-specific evidence.",
            "evidence": [{"source_id": "S2", "quote": source["excerpt"]}],
        }
        return LLMResponse(
            text=json.dumps(
                {
                    "category": [insight],
                    "market_expectations": [insight],
                    "offers": [insight],
                    "customer_expectations": [insight],
                    "positioning_patterns": [insight],
                    "opportunities": [insight],
                }
            ),
            provider="test-llm",
            model="sparse-evidence",
        )


@pytest.mark.asyncio
async def test_uncorroborated_observations_are_capped_not_discarded() -> None:
    """Retrieval decides topical fit; observation wording only caps confidence.

    The evidence here is genuinely grounded (verbatim, from a source the
    dimension's own query returned), so it is kept rather than silently
    dropped, but the observation's wording corroborates nothing and must not
    be allowed to claim high confidence.
    """
    tool = MarketResearchTool(
        _SearchProvider(),
        analyzer=LLMResearchAnalyzer(_SparseEvidenceLLM()),
    )
    report = await tool.research(
        _context(),
        researched_at=datetime(2026, 8, 25, 10, tzinfo=UTC),
        ttl_seconds=600,
    )

    assert isinstance(report.analysis, MarketResearchAnalysis)
    assert report.analysis.category
    assert report.analysis.positioning_patterns
    for insight in (*report.analysis.category, *report.analysis.positioning_patterns):
        assert insight.confidence is not ResearchConfidence.HIGH
    assert report.evidence_coverage is not None
    assert report.evidence_coverage.missing_dimensions == []
    assert report.evidence_coverage.coverage_ratio == 1
    assert any(
        "capped below high confidence" in limitation
        for limitation in report.evidence_coverage.limitations
    )


class _LowRelevanceSearch:
    async def search(self, request: ResearchRequest) -> ResearchResponse:
        return ResearchResponse(
            results=(
                ResearchResult(
                    title="Weakly related result",
                    url="https://example.test/weak",
                    content="A generic result without reliable relevance.",
                    # Below MIN_RELEVANCE_SCORE: junk the provider matched only
                    # loosely, not a merely mediocre page.
                    score=0.2,
                ),
            ),
            provider="test-search",
            query=request.query,
        )


@pytest.mark.asyncio
async def test_low_relevance_results_are_filtered_before_analysis() -> None:
    llm = _AnalysisLLM()
    tool = SocialResearchTool(
        _LowRelevanceSearch(),
        analyzer=LLMResearchAnalyzer(llm),
    )
    report = await tool.research(
        _context(),
        researched_at=datetime.now(UTC),
        ttl_seconds=600,
    )

    assert report.status is ResearchStatus.NO_RESULTS
    assert report.sources == []
    assert llm.requests == []


def test_source_quality_combines_relevance_authority_locality_and_freshness() -> None:
    researched_at = datetime(2026, 8, 25, 10, tzinfo=UTC)
    source = source_from_result(
        ResearchResult(
            title="Kosovo airport rental statistics",
            url="https://transport.gov/reports/kosovo-rentals",
            content="Prishtina airport rental demand increased during the summer season.",
            score=0.9,
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
        dimension="category",
        context=_context(),
        researched_at=researched_at,
    )

    assert source is not None
    assert source.source_type.value == "government"
    assert source.authority_score == 0.95
    assert source.locality_score == 1
    assert source.freshness_score == 1
    assert source.quality_score > 0.9
