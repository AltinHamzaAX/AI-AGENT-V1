import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from test_art_director_agent import _ArtLLM
from test_art_director_agent import _input as _art_input
from test_art_director_agent import _run as _run_art

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.design_spec import (
    DESIGN_SPEC_DEFINITION,
    DESIGN_SPEC_SCHEMA_VERSION,
    DesignSpec,
    DesignSpecAgent,
    DesignSpecInput,
)
from app.modules.posts.agents.framework import AgentExecutionContext
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration.design_spec import (
    DesignSpecStageHandler,
    _agent_payload,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
)


def _spec_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "canvas": {"width": 1080, "height": 1080, "unit": "px"},
        "safe_area": {"top": 64, "right": 64, "bottom": 64, "left": 64},
        "grid": {"columns": 12, "rows": 12, "gutter": 24, "baseline": 8},
        "regions": {
            "product_bounds": {"x": 0, "y": 220, "width": 650, "height": 760},
            "headline_region": {"x": 680, "y": 96, "width": 320, "height": 180},
            "offer_region": {"x": 680, "y": 500, "width": 220, "height": 80},
            "cta_region": {"x": 680, "y": 620, "width": 260, "height": 72},
            "logo_region": {"x": 820, "y": 920, "width": 180, "height": 72},
        },
        "typography_roles": [
            {"role": "headline", "family_token": "brand-display", "weight": 700,
             "size_px": 64, "line_height": 1.05, "max_lines": 3, "align": "left"},
            {"role": "supporting_copy", "family_token": "brand-body", "weight": 400,
             "size_px": 28, "line_height": 1.3, "max_lines": 4, "align": "left"},
            {"role": "offer", "family_token": "brand-display", "weight": 700,
             "size_px": 34, "line_height": 1.0, "max_lines": 2, "align": "left"},
            {"role": "cta", "family_token": "brand-body", "weight": 700,
             "size_px": 26, "line_height": 1.0, "max_lines": 1, "align": "center"},
        ],
        "color_system": [
            {"role": "background", "value": "#F4F1EA", "source": "neutral"},
            {"role": "text", "value": "#171717", "source": "neutral"},
            {"role": "accent", "value": "#D98A2B", "source": "brand"},
            {"role": "cta_background", "value": "#171717", "source": "neutral"},
            {"role": "cta_text", "value": "#FFFFFF", "source": "neutral"},
        ],
        "graphic_elements": [
            {"kind": "motif", "region": {"x": 400, "y": 160, "width": 500,
             "height": 24}, "color_role": "accent", "opacity": 0.8,
             "decorative_only": True}
        ],
        "photography": "Original vehicle at a welcoming three-quarter arrival angle.",
        "lighting": "Soft directional daylight with natural reflections.",
        "background": "Warm neutral arrival environment with a restrained doorstep motif.",
    }


class _DesignSpecLLM:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = responses or [_spec_payload()]
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        value = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(text=json.dumps(value), provider="test-llm", model="design-spec-test")


async def _input() -> DesignSpecInput:
    art_input = await _art_input()
    art = await _run_art(art_input, _ArtLLM())
    return DesignSpecInput(
        art_direction=art,
        copy_draft=art_input.copy_draft,
        semantic_contract=art_input.semantic_contract,
    )


def _context() -> AgentExecutionContext:
    return AgentExecutionContext(
        invocation=InvocationContext(
            correlation_id=uuid4(), post_id=uuid4(), generation_id=uuid4()
        ),
        agent_name="design_spec_compiler",
        attempt=1,
    )


async def _run(payload: DesignSpecInput, llm: _DesignSpecLLM) -> DesignSpec:
    return await DesignSpecAgent(llm).execute(payload, None, _context())


@pytest.mark.asyncio
async def test_returns_complete_versioned_machine_readable_spec() -> None:
    payload = await _input()
    result = await _run(payload, _DesignSpecLLM())
    assert result.schema_version == DESIGN_SPEC_SCHEMA_VERSION
    assert result.canvas.width == 1080
    assert result.regions.product_bounds.width == 650
    assert result.regions.headline_region.x == 680
    assert result.regions.offer_region is not None
    assert result.regions.cta_region.width == 260
    assert result.regions.logo_region.x == 820
    assert result.typography_roles and result.color_system
    assert result.graphic_elements and result.photography and result.lighting and result.background
    assert result.contract_fingerprint == payload.art_direction.contract_fingerprint


@pytest.mark.asyncio
async def test_out_of_canvas_geometry_is_repaired() -> None:
    invalid = deepcopy(_spec_payload())
    invalid["regions"]["headline_region"]["x"] = 1000
    result = await _run(await _input(), _DesignSpecLLM([invalid, _spec_payload()]))
    assert result.regions.headline_region.x == 680


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["version", "safe_area", "offer"])
async def test_repeated_invalid_geometry_uses_composer_safe_fallback(mutation: str) -> None:
    invalid = deepcopy(_spec_payload())
    if mutation == "version":
        invalid["schema_version"] = "2.0"
    elif mutation == "safe_area":
        invalid["regions"]["logo_region"]["x"] = 1010
    else:
        invalid["regions"]["offer_region"] = None
    payload = await _input()
    result = await _run(payload, _DesignSpecLLM([invalid, invalid]))

    assert result.schema_version == "1.0"
    assert result.regions.logo_region.x + result.regions.logo_region.width <= 1016
    assert (result.regions.offer_region is not None) == (
        payload.copy_draft.offer_copy is not None
    )


@pytest.mark.asyncio
async def test_semantic_drift_is_rejected_before_provider() -> None:
    payload = await _input()
    drifted = payload.model_copy(
        update={
            "copy_draft": payload.copy_draft.model_copy(
                update={"contract_fingerprint": "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        DesignSpecInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_stage_writes_only_design_spec() -> None:
    payload = await _input()
    state = {
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
        PostWorkflowSection.ART_DIRECTION.value: payload.art_direction.model_dump(mode="json"),
        PostWorkflowSection.COPY.value: payload.copy_draft.model_dump(mode="json"),
    }
    providers = ProviderBundle(
        llm=_DesignSpecLLM(), vision=MockVisionProvider(), image=MockImageProvider(),
        embedding=MockEmbeddingProvider(), research=MockResearchProvider(),
        storage=MockStorageProvider(),
    )
    context = SupervisorStageContext(
        generation_id=uuid4(), post_id=uuid4(), job_id=uuid4(), workflow_state=state,
        state_version=1, action=SupervisorAction.CONTINUE,
    )
    result = await DesignSpecStageHandler(providers).execute(context)
    assert set(result.outputs) == {PostWorkflowSection.DESIGN_SPEC}
    assert result.outputs[PostWorkflowSection.DESIGN_SPEC]["schema_version"] == "1.0"
    assert _agent_payload(state)["art_direction"]


def test_supervisor_declares_design_spec_contract_inputs() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.DESIGN_SPEC)
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.COPY,
        PostWorkflowSection.ART_DIRECTION,
    }
    assert policy.output_sections == (PostWorkflowSection.DESIGN_SPEC,)


def test_compiler_has_no_tools_and_schema_forbids_free_form_layout() -> None:
    assert DESIGN_SPEC_DEFINITION.allowed_tools == frozenset()
    schema = json.dumps(DesignSpec.model_json_schema())
    assert '"additionalProperties": false' in schema
    assert "product_bounds" in schema and "typography_roles" in schema
