"""Ticket 20: visual reference, trend and platform as structured engines.

Three tools that used to run one search and return sources now analyze what
they find across named dimensions. Two things are worth pinning beyond the
schema: fourteen visual attributes must not cost fourteen searches, and a
trend must not become usable just because a model says it is.
"""

import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderResponseError,
    ResearchImage,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    LLMResearchAnalyzer,
    PlatformResearchTool,
    ResearchConfidence,
    ResearchContext,
    ResearchStatus,
    TrendResearchTool,
    VisualReferenceTool,
)
from app.modules.posts.tools.research.schemas import (
    PlatformAnalysis,
    TrendAnalysis,
    VisualReferenceAnalysis,
)

NOW = datetime(2026, 8, 25, 10, tzinfo=UTC)

#: One page that genuinely speaks to every dimension, so grounding is decided
#: by the rules under test rather than by a thin fixture.
PAGE = (
    "Observed rental creative in Prishtina keeps the composition centred with a wide crop, "
    "and the headline sits in the upper banner region above generous white space. "
    "Typography is a heavy sans overlay, the call to action reads Book now, and the logo "
    "stays in the outer corner. Photography is daylight product photography with soft "
    "shadow, a warm colour palette, matte texture, and small price badges. The mood is "
    "calm rather than dynamic. Reels remain the current format, vertical stills are "
    "emerging, heavy filter overlays are overused, and static carousels are declining. "
    "Instagram accepts 1080 by 1350 pixel images and video up to 90 seconds, with a "
    "caption limit of 2200 characters."
)

VISUAL_DIMENSIONS = tuple(VisualReferenceAnalysis.model_fields)
TREND_STAGES = tuple(TrendAnalysis.model_fields)
PLATFORM_DIMENSIONS = tuple(PlatformAnalysis.model_fields)


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


class _SearchProvider:
    """One distinct page per distinct query, so grouping is observable."""

    def __init__(self, *, images: tuple[ResearchImage, ...] = ()) -> None:
        self.requests: list[ResearchRequest] = []
        self._images = images

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        digest = sha256(request.query.encode()).hexdigest()[:12]
        return ResearchResponse(
            results=(
                ResearchResult(
                    title=f"Observed creative {digest}",
                    url=f"https://reference.example/{digest}",
                    content=PAGE,
                    score=0.9,
                ),
            ),
            provider="test-search",
            query=request.query,
            images=self._images,
            answer=None,
        )


class _AnalysisLLM:
    """Fills every dimension, citing a source that was fetched for it."""

    def __init__(
        self,
        *,
        brand_fit: bool = True,
        audience_fit: bool = True,
        objective_fit: bool = True,
        forge_usable: bool = False,
    ) -> None:
        self.requests: list[LLMRequest] = []
        self._fits = {
            "brand_fit": brand_fit,
            "audience_fit": audience_fit,
            "objective_fit": objective_fit,
        }
        self._forge_usable = forge_usable

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        prompt = request.messages[0].content
        sources = json.loads(request.messages[-1].content)["sources"]

        def insight(dimension: str, extra: dict | None = None) -> dict:
            # Prefer a source that was actually retrieved for this dimension,
            # which is what the prompt asks the model to do.
            source = next(
                (item for item in sources if dimension in item["allowed_dimensions"]),
                sources[0],
            )
            body = {
                "observation": f"Observed {dimension.replace('_', ' ')} pattern in the market.",
                "evidence": [{"source_id": source["id"], "quote": source["excerpt"][:120]}],
            }
            body.update(extra or {})
            return body

        if "negative_space" in prompt:
            payload = {dimension: [insight(dimension)] for dimension in VISUAL_DIMENSIONS}
        elif "objective_fit" in prompt:
            extra = dict(self._fits)
            if self._forge_usable:
                extra["usable"] = True
            payload = {stage: [insight(stage, extra)] for stage in TREND_STAGES}
        else:
            payload = {dimension: [insight(dimension)] for dimension in PLATFORM_DIMENSIONS}
        return LLMResponse(text=json.dumps(payload), provider="test-llm", model="t20")


# --------------------------------------------------------------------------
# Visual reference
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visual_reference_analyses_all_fourteen_attributes() -> None:
    tool = VisualReferenceTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_AnalysisLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert isinstance(report.analysis, VisualReferenceAnalysis)
    assert len(VISUAL_DIMENSIONS) == 14
    for dimension in VISUAL_DIMENSIONS:
        assert getattr(report.analysis, dimension), dimension
    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1


@pytest.mark.asyncio
async def test_fourteen_attributes_do_not_cost_fourteen_searches() -> None:
    """Dimensions are separate on the report; their queries are not.

    A page showing layout shows framing, crop and headline position in the
    same breath, so asking fourteen times would buy the same pages fourteen
    times over. Provenance still has to survive the saving.
    """
    provider = _SearchProvider()
    tool = VisualReferenceTool(provider, analyzer=LLMResearchAnalyzer(_AnalysisLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert len(provider.requests) == 4, "one search per kind of page, not per attribute"
    queried = {query for query in (request.query for request in provider.requests)}
    assert len(queried) == 4, "the four searches must be genuinely different questions"
    credited = {dimension for source in report.sources for dimension in source.dimensions}
    assert credited == set(VISUAL_DIMENSIONS), "every attribute keeps its own provenance"


@pytest.mark.asyncio
async def test_visual_references_survive_the_move_to_structured_analysis() -> None:
    """Images are the point of this category and are collected per search."""
    provider = _SearchProvider(images=(ResearchImage(url="https://cdn.example/ad.jpg"),))
    tool = VisualReferenceTool(provider, analyzer=LLMResearchAnalyzer(_AnalysisLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert [str(reference.url) for reference in report.visual_references] == [
        "https://cdn.example/ad.jpg"
    ], "the same image found by four searches is one reference"
    assert all(request.include_images for request in provider.requests)


# --------------------------------------------------------------------------
# Trend usability
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trend_is_usable_only_when_all_three_fits_hold() -> None:
    tool = TrendResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_AnalysisLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert isinstance(report.analysis, TrendAnalysis)
    assert report.analysis.usable, "three fits held, so the trends are usable"
    for stage in TREND_STAGES:
        for insight in getattr(report.analysis, stage):
            assert insight.usable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["brand_fit", "audience_fit", "objective_fit"])
async def test_one_failing_fit_is_enough_to_make_a_trend_unusable(missing: str) -> None:
    llm = _AnalysisLLM(**{missing: False})
    tool = TrendResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(llm))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert report.analysis.usable == [], f"{missing} was false"
    for stage in TREND_STAGES:
        for insight in getattr(report.analysis, stage):
            assert insight.usable is False
            assert getattr(insight, missing) is False


@pytest.mark.asyncio
async def test_an_unusable_trend_is_still_reported_as_evidence() -> None:
    """Knowing a trend does not fit is a finding, not a reason to hide it."""
    llm = _AnalysisLLM(brand_fit=False)
    tool = TrendResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(llm))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert report.status is ResearchStatus.SUCCEEDED
    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1
    for stage in TREND_STAGES:
        insights = getattr(report.analysis, stage)
        assert insights, stage
        assert all(insight.evidence for insight in insights)


@pytest.mark.asyncio
async def test_a_model_cannot_declare_a_trend_usable() -> None:
    """Usability is ours to compute, exactly as competitor safe_use is."""
    llm = _AnalysisLLM(objective_fit=False, forge_usable=True)
    tool = TrendResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(llm))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert report.analysis.usable == [], "an asserted usable flag must not survive"


@pytest.mark.asyncio
async def test_the_trend_prompt_asks_for_three_independent_fits() -> None:
    llm = _AnalysisLLM()
    tool = TrendResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(llm))

    await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    prompt = llm.requests[0].messages[0].content
    assert "brand_fit" in prompt and "audience_fit" in prompt and "objective_fit" in prompt
    assert "not yours to set" in prompt, "the model must not be asked to decide usability"
    subject = json.loads(llm.requests[0].messages[-1].content)["subject"]
    assert subject["objective"] == "Increase airport pickup bookings"


@pytest.mark.asyncio
async def test_trend_retrieval_is_wider_than_the_brief() -> None:
    """The fit gate applies the brief, so retrieval must not apply it twice.

    Measured live, naming the exact entity and market on the news index
    returned one usable source in five, and nothing at all for overused
    patterns. Three fits cannot judge an empty result set.
    """
    provider = _SearchProvider()
    context = _context()

    await TrendResearchTool(provider).research(context, researched_at=NOW, ttl_seconds=600)

    assert len(provider.requests) == len(TREND_STAGES)
    assert all(request.topic == "general" for request in provider.requests)
    assert all(request.time_range is None for request in provider.requests)
    assert all(request.country is None for request in provider.requests)
    queries = " ".join(request.query for request in provider.requests)
    assert context.market not in queries, "the market is applied by the fit gate"


# --------------------------------------------------------------------------
# Platform
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_analysis_separates_formats_from_constraints() -> None:
    tool = PlatformResearchTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_AnalysisLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert isinstance(report.analysis, PlatformAnalysis)
    assert report.analysis.formats
    assert report.analysis.constraints
    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1


@pytest.mark.asyncio
async def test_platform_specifications_come_from_the_platform_itself() -> None:
    provider = _SearchProvider()
    await PlatformResearchTool(provider).research(_context(), researched_at=NOW, ttl_seconds=600)

    assert provider.requests
    for request in provider.requests:
        assert request.include_domains, "specifications are not whoever ranks for them"
        assert request.country is None, "platform specifications are global"


@pytest.mark.asyncio
async def test_an_analysis_that_grounds_nothing_still_fails_loudly() -> None:
    class _EmptyLLM:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text=json.dumps({}), provider="x", model="y")

    tool = VisualReferenceTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_EmptyLLM()))

    with pytest.raises(
        ProviderResponseError,
        match="visual_reference research returned invalid structured analysis",
    ):
        await tool.research(_context(), researched_at=NOW, ttl_seconds=600)


@pytest.mark.asyncio
async def test_an_uncited_dimension_does_not_cost_the_other_thirteen() -> None:
    """Observed live: a model answered fourteen dimensions and cited eleven.

    Requiring a citation in the draft schema made pydantic reject the whole
    response, so three uncited attributes destroyed eleven grounded ones.
    """
    uncited = {"subject_scale", "energy", "texture"}

    class _PartlyUncitedLLM(_AnalysisLLM):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            sources = json.loads(request.messages[-1].content)["sources"]
            payload = {
                dimension: [
                    {
                        "observation": f"Observed {dimension} pattern.",
                        "evidence": []
                        if dimension in uncited
                        else [
                            {
                                "source_id": sources[0]["id"],
                                "quote": sources[0]["excerpt"][:120],
                            }
                        ],
                    }
                ]
                for dimension in VISUAL_DIMENSIONS
            }
            return LLMResponse(text=json.dumps(payload), provider="x", model="y")

    tool = VisualReferenceTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_PartlyUncitedLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    covered = set(report.evidence_coverage.covered_dimensions)
    assert covered == set(VISUAL_DIMENSIONS) - uncited
    assert set(report.evidence_coverage.missing_dimensions) == uncited
    assert any(
        limitation.startswith("Unverifiable evidence was discarded for:")
        for limitation in report.evidence_coverage.limitations
    )


@pytest.mark.asyncio
async def test_off_provenance_evidence_is_capped_not_discarded() -> None:
    """Citing a page fetched for another dimension weakens, never destroys."""

    class _SingleSourceLLM(_AnalysisLLM):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            sources = json.loads(request.messages[-1].content)["sources"]
            first = sources[0]
            payload = {
                dimension: [
                    {
                        "observation": f"Observed {dimension} pattern.",
                        "evidence": [{"source_id": first["id"], "quote": first["excerpt"][:120]}],
                    }
                ]
                for dimension in VISUAL_DIMENSIONS
            }
            return LLMResponse(text=json.dumps(payload), provider="x", model="y")

    tool = VisualReferenceTool(_SearchProvider(), analyzer=LLMResearchAnalyzer(_SingleSourceLLM()))

    report = await tool.research(_context(), researched_at=NOW, ttl_seconds=600)

    assert report.evidence_coverage is not None
    assert report.evidence_coverage.coverage_ratio == 1
    off = [
        insight
        for dimension in VISUAL_DIMENSIONS
        if dimension not in report.sources[0].dimensions
        for insight in getattr(report.analysis, dimension)
    ]
    assert off, "the fixture must actually produce off-provenance citations"
    assert all(insight.confidence is not ResearchConfidence.HIGH for insight in off)
