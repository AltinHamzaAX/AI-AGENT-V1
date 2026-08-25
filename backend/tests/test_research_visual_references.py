"""Visual reference collection.

`VisualReferenceTool` exists to observe what the market's advertising looks
like. Without images it could only ever find pages *about* visuals, so these
tests pin that images are requested, parsed, and carried as references — and
that they stay references rather than becoming design instructions.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.tavily import TavilyResearchProvider
from app.modules.posts.providers import (
    ResearchImage,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
)
from app.modules.posts.tools.research import (
    AudienceResearchTool,
    MarketResearchTool,
    PlatformResearchTool,
    ResearchCategory,
    ResearchConfidence,
    ResearchContext,
    ResearchReport,
    ResearchStatus,
    ResearchVisualReference,
    VisualReferenceTool,
)

NOW = datetime(2026, 8, 25, 10, tzinfo=UTC)


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


class _ImageProvider:
    def __init__(self, *, images=(), results=None) -> None:
        self.requests: list[ResearchRequest] = []
        self._images = images
        self._results = (
            results
            if results is not None
            else (
                ResearchResult(
                    title="Rental advertising roundup",
                    url="https://ex.example/visual",
                    content="Observed advertising imagery and composition.",
                    score=0.9,
                ),
            )
        )

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        return ResearchResponse(
            results=self._results,
            provider="test",
            query=request.query,
            answer=None,
            images=self._images,
        )


# --------------------------------------------------------------------------
# Requesting images
# --------------------------------------------------------------------------


async def test_visual_reference_tool_asks_the_provider_for_images() -> None:
    provider = _ImageProvider()
    await VisualReferenceTool(provider).research(_context(), researched_at=NOW, ttl_seconds=600)
    assert provider.requests[0].include_images is True


@pytest.mark.parametrize(
    "tool_type", [MarketResearchTool, AudienceResearchTool, PlatformResearchTool]
)
async def test_text_tools_do_not_pay_for_images(tool_type) -> None:
    provider = _ImageProvider()
    await tool_type(provider).research(_context(), researched_at=NOW, ttl_seconds=600)
    assert all(request.include_images is False for request in provider.requests)


def test_image_collection_separates_the_cache_variant() -> None:
    provider = _ImageProvider()
    assert "images" in VisualReferenceTool(provider).cache_variant
    assert "text" in AudienceResearchTool(provider).cache_variant


# --------------------------------------------------------------------------
# Carrying images into the report
# --------------------------------------------------------------------------


async def test_images_become_timestamped_described_references() -> None:
    provider = _ImageProvider(
        images=(
            ResearchImage(
                url="https://cdn.example/ad-one.jpg",
                description="Airport pickup counter with branded signage",
            ),
            ResearchImage(url="https://cdn.example/ad-two.jpg"),
        )
    )

    report = await VisualReferenceTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    assert report.category is ResearchCategory.VISUAL_REFERENCE
    assert len(report.visual_references) == 2
    first, second = report.visual_references
    assert str(first.url) == "https://cdn.example/ad-one.jpg"
    assert first.description == "Airport pickup counter with branded signage"
    assert first.retrieved_at == NOW
    assert second.description is None


async def test_images_alone_are_evidence_enough_to_succeed() -> None:
    provider = _ImageProvider(
        images=(ResearchImage(url="https://cdn.example/ad.jpg"),),
        results=(),
    )

    report = await VisualReferenceTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    assert report.status is ResearchStatus.SUCCEEDED
    assert report.sources == []
    assert report.visual_references


async def test_no_images_and_no_sources_is_still_no_results() -> None:
    provider = _ImageProvider(images=(), results=())

    report = await VisualReferenceTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    assert report.status is ResearchStatus.NO_RESULTS
    assert report.visual_references == []


async def test_duplicate_and_unusable_images_are_dropped_not_fatal() -> None:
    provider = _ImageProvider(
        images=(
            ResearchImage(url="https://cdn.example/ad.jpg"),
            ResearchImage(url="https://cdn.example/ad.jpg"),
            ResearchImage(url="not-a-url"),
            ResearchImage(url="https://cdn.example/other.jpg"),
        )
    )

    report = await VisualReferenceTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    urls = [str(reference.url) for reference in report.visual_references]
    assert urls == ["https://cdn.example/ad.jpg", "https://cdn.example/other.jpg"]


async def test_visual_references_are_bounded() -> None:
    provider = _ImageProvider(
        images=tuple(ResearchImage(url=f"https://cdn.example/{n}.jpg") for n in range(50))
    )

    report = await VisualReferenceTool(provider).research(
        _context(), researched_at=NOW, ttl_seconds=600
    )

    assert len(report.visual_references) == 20


# --------------------------------------------------------------------------
# Tavily parsing
# --------------------------------------------------------------------------


def _tavily_body(images):
    return {
        "query": "visual references",
        "answer": None,
        "results": [
            {
                "title": "Roundup",
                "url": "https://ex.example/a",
                "content": "Observed imagery.",
                "score": 0.9,
            }
        ],
        "images": images,
    }


async def _search(images, *, include_images=True):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_tavily_body(images))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-test", client=client)
        response = await provider.search(
            ResearchRequest(query="visual references", include_images=include_images)
        )
    return response, captured


async def test_tavily_requests_descriptions_alongside_images() -> None:
    _, payload = await _search([])
    assert payload["include_images"] is True
    assert payload["include_image_descriptions"] is True

    _, payload = await _search([], include_images=False)
    assert payload["include_images"] is False
    assert payload["include_image_descriptions"] is False


async def test_tavily_parses_described_image_objects() -> None:
    response, _ = await _search(
        [{"url": "https://cdn.example/a.jpg", "description": "Branded counter"}]
    )
    assert response.images == (
        ResearchImage(url="https://cdn.example/a.jpg", description="Branded counter"),
    )


async def test_tavily_tolerates_bare_url_strings_and_skips_junk() -> None:
    response, _ = await _search(
        [
            "https://cdn.example/a.jpg",
            {"url": "https://cdn.example/b.jpg"},
            {"description": "no url"},
            {"url": 42},
            "",
            "https://cdn.example/a.jpg",
            None,
        ]
    )

    assert [image.url for image in response.images] == [
        "https://cdn.example/a.jpg",
        "https://cdn.example/b.jpg",
    ]
    assert response.images[0].description is None


async def test_tavily_missing_images_block_is_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _tavily_body([])
        del body["images"]
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-test", client=client)
        response = await provider.search(ResearchRequest(query="visual references"))

    assert response.images == ()
    assert response.results


# --------------------------------------------------------------------------
# Boundary
# --------------------------------------------------------------------------


def test_visual_reference_carries_no_design_instruction() -> None:
    with pytest.raises(ValidationError):
        ResearchVisualReference.model_validate(
            {
                "url": "https://cdn.example/a.jpg",
                "retrieved_at": NOW,
                "art_direction": "use this layout",
            }
        )


def test_research_without_results_cannot_carry_visual_references() -> None:
    with pytest.raises(ValidationError, match="cannot contain evidence"):
        ResearchReport.model_validate(
            {
                "category": ResearchCategory.VISUAL_REFERENCE,
                "status": ResearchStatus.NO_RESULTS,
                "query": "visual references",
                "provider": "test",
                "confidence": ResearchConfidence.LOW,
                "researched_at": NOW,
                "expires_at": datetime(2026, 8, 25, 11, tzinfo=UTC),
                "cache_key": "c" * 64,
                "visual_references": [
                    {"url": "https://cdn.example/a.jpg", "retrieved_at": NOW},
                ],
            }
        )
