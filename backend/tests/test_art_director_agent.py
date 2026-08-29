import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from test_copywriter_agent import _CopyLLM
from test_copywriter_agent import _input as _copy_input
from test_copywriter_agent import _run as _run_copy

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.art_director import (
    ART_DIRECTOR_DEFINITION,
    ArtDirection,
    ArtDirectorAgent,
    ArtDirectorInput,
    HierarchyElement,
)
from app.modules.posts.agents.asset_intelligence import AssetIntelligenceResult
from app.modules.posts.agents.framework import AgentExecutionContext
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration.art_direction import (
    ArtDirectionStageHandler,
    _agent_payload,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ProviderResponseError,
)


def _art_payload() -> dict[str, Any]:
    return {
        "focal_point": "The vehicle and doorstep motif form the primary focal point.",
        "composition": (
            "Use an asymmetric mobile-first frame that carries the doorstep motif "
            "from the vehicle toward the open copy area."
        ),
        "visual_hierarchy": [
            {"rank": 1, "element": "product", "reason": "Lead with the vehicle."},
            {"rank": 2, "element": "headline", "reason": "State the arrival idea."},
            {"rank": 3, "element": "supporting_copy", "reason": "Clarify the benefit."},
            {"rank": 4, "element": "offer", "reason": "Surface the approved price."},
            {"rank": 5, "element": "cta", "reason": "Close with the action."},
            {"rank": 6, "element": "logo", "reason": "Finish with brand ownership."},
        ],
        "product_dominance": 0.55,
        "negative_space": "Reserve calm negative space for headline, offer and CTA copy.",
        "photography_direction": (
            "Photograph the original vehicle at a welcoming three-quarter angle near arrival."
        ),
        "lighting": "Use soft directional daylight with natural reflections and gentle depth.",
        "typography_direction": (
            "Use a confident display weight for the headline and a compact supporting scale."
        ),
        "color_direction": "Use the verified brand palette with restrained neutral support.",
        "graphic_language": (
            "Extend the doorstep motif as a clean path connecting arrival and movement."
        ),
        "cta_treatment": "Use a high-contrast, mobile-readable CTA with a clear tap area.",
        "logo_region": "Keep the logo in a quiet, clear safe region at the closing edge.",
    }


class _ArtLLM:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = responses or [_art_payload()]
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(
            text=json.dumps(response), provider="test-llm", model="art-director-test"
        )


async def _input() -> ArtDirectorInput:
    copy_input = await _copy_input()
    copy = await _run_copy(copy_input, _CopyLLM())
    contract = PostSemanticContract.from_dict(copy_input.semantic_contract)
    return ArtDirectorInput(
        concept=copy_input.concept,
        copy_draft=copy,
        brand=copy_input.brand_voice,
        assets=AssetIntelligenceResult(assets=[], contract_fingerprint=contract.fingerprint),
        platform=contract.platform,
        semantic_contract=contract.to_dict(),
    )


def _context() -> AgentExecutionContext:
    return AgentExecutionContext(
        invocation=InvocationContext(
            correlation_id=uuid4(), post_id=uuid4(), generation_id=uuid4()
        ),
        agent_name="art_director",
        attempt=1,
    )


async def _run(payload: ArtDirectorInput, llm: _ArtLLM) -> ArtDirection:
    return await ArtDirectorAgent(llm).execute(payload, None, _context())


@pytest.mark.asyncio
async def test_art_director_returns_all_ticket_fields_and_quality_checks() -> None:
    payload = await _input()
    result = await _run(payload, _ArtLLM())
    for field in (
        "focal_point", "composition", "negative_space", "photography_direction",
        "lighting", "typography_direction", "color_direction", "graphic_language",
        "cta_treatment", "logo_region",
    ):
        assert getattr(result, field)
    assert result.product_dominance == 0.55
    assert result.quality.failures == []
    assert len(result.quality.checks) == 5
    assert result.contract_fingerprint == payload.copy_draft.contract_fingerprint


@pytest.mark.asyncio
async def test_visual_hierarchy_matches_the_approved_copy_flow() -> None:
    result = await _run(await _input(), _ArtLLM())
    assert [step.element for step in result.visual_hierarchy] == [
        HierarchyElement.PRODUCT, HierarchyElement.HEADLINE,
        HierarchyElement.SUPPORTING_COPY, HierarchyElement.OFFER,
        HierarchyElement.CTA, HierarchyElement.LOGO,
    ]


@pytest.mark.asyncio
async def test_only_winning_concept_reaches_art_direction_prompt() -> None:
    payload = await _input()
    llm = _ArtLLM()
    await _run(payload, llm)
    source = json.loads(llm.requests[0].messages[-1].content)["source"]
    assert source["winning_concept"]["id"] == payload.concept.winning_concept.candidate_id
    assert "rejected_concepts" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_dominance", 0.1),
        ("color_direction", "Invent a new #FF00AA accent."),
        ("logo_region", "Replace the logo with a cleaner brand mark."),
    ],
)
async def test_unsafe_direction_fails_closed(field: str, value: Any) -> None:
    invalid = deepcopy(_art_payload())
    invalid[field] = value
    with pytest.raises(ProviderResponseError):
        await _run(await _input(), _ArtLLM([invalid, invalid]))


@pytest.mark.asyncio
async def test_invalid_hierarchy_is_repaired_as_a_complete_output() -> None:
    invalid = deepcopy(_art_payload())
    invalid["visual_hierarchy"][0]["element"] = "headline"
    result = await _run(await _input(), _ArtLLM([invalid, _art_payload()]))
    assert result.visual_hierarchy[0].element is HierarchyElement.PRODUCT


@pytest.mark.asyncio
async def test_contract_drift_is_rejected_before_provider_call() -> None:
    payload = await _input()
    drifted = payload.model_copy(
        update={"assets": payload.assets.model_copy(update={"contract_fingerprint": "0" * 64})}
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        ArtDirectorInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_stage_writes_only_art_direction() -> None:
    payload = await _input()
    providers = ProviderBundle(
        llm=_ArtLLM(), vision=MockVisionProvider(), image=MockImageProvider(),
        embedding=MockEmbeddingProvider(), research=MockResearchProvider(),
        storage=MockStorageProvider(),
    )
    state = {
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
        PostWorkflowSection.CREATIVE_CONCEPT.value: payload.concept.model_dump(mode="json"),
        PostWorkflowSection.COPY.value: payload.copy_draft.model_dump(mode="json"),
        PostWorkflowSection.BRAND.value: payload.brand.model_dump(mode="json"),
        PostWorkflowSection.ASSETS.value: [
            asset.model_dump(mode="json") for asset in payload.assets.assets
        ],
    }
    context = SupervisorStageContext(
        generation_id=uuid4(), post_id=uuid4(), job_id=uuid4(), workflow_state=state,
        state_version=1, action=SupervisorAction.CONTINUE,
    )
    result = await ArtDirectionStageHandler(providers).execute(context)
    assert set(result.outputs) == {PostWorkflowSection.ART_DIRECTION}
    assert _agent_payload(state)["platform"] == payload.platform


def test_supervisor_declares_all_art_director_inputs() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.ART_DIRECTION)
    assert set(policy.dependencies) == {
        SupervisorStage.COPYWRITING, SupervisorStage.ASSET_INTELLIGENCE,
    }
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT, PostWorkflowSection.CREATIVE_CONCEPT,
        PostWorkflowSection.COPY, PostWorkflowSection.BRAND, PostWorkflowSection.ASSETS,
    }
    assert policy.output_sections == (PostWorkflowSection.ART_DIRECTION,)


def test_art_director_has_no_tools_or_production_output() -> None:
    assert ART_DIRECTOR_DEFINITION.allowed_tools == frozenset()
    schema = json.dumps(ArtDirection.model_json_schema()).casefold()
    for forbidden in ("image_prompt", "generated_image", "svg", "css", "final_layout"):
        assert forbidden not in schema
