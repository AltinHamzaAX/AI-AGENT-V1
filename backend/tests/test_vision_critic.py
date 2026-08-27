from typing import Any

import pytest
from pydantic import ValidationError
from test_design_critic import _context, _providers, _state

from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.agents.vision_critic import (
    VisionCritic,
    VisionCriticDecision,
    VisionCriticInput,
    VisionDimension,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage
from app.modules.posts.orchestration import VisionCriticStageHandler
from app.modules.posts.providers import ProviderResponseError, VisionRequest, VisionResponse
from app.modules.posts.tools.composition import PostDraft


class _Vision:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return VisionResponse(
            data=self.responses[index] if self.responses else {},
            provider="test-vision",
            model="vision-critic-test",
        )


def _readout(*, failing: VisionDimension | None = None) -> dict[str, Any]:
    issues = []
    if failing:
        issues.append(
            {
                "dimension": failing.value,
                "issue": "Product overwhelms the approved headline hierarchy.",
                "region": "center product and upper headline",
                "severity": "high",
                "confidence": 0.96,
                "expected": "Headline is the dominant first-read element.",
                "observed": "Product occupies most visual weight and is seen first.",
                "recommended_action": "Reduce product scale and restore headline primacy.",
            }
        )
    return {
        "assessed_dimensions": [item.value for item in VisionDimension],
        "issues": issues,
        "summary": "Compared the approved design with the actual final render.",
    }


async def _payload() -> tuple[dict[str, Any], Any, VisionCriticInput]:
    state, storage, design_payload = await _state()
    draft = PostDraft.model_validate(state[PostWorkflowSection.POST_DRAFT.value])
    image = await storage.get(key=draft.final_asset.storage_key)
    payload = VisionCriticInput(
        final_image=image,
        final_mime_type=draft.final_asset.mime_type,
        semantic_contract=state[PostWorkflowSection.SEMANTIC_CONTRACT.value],
        copy_draft=CopyDraft.model_validate(state[PostWorkflowSection.COPY.value]),
        design_spec=DesignSpec.model_validate(state[PostWorkflowSection.DESIGN_SPEC.value]),
        post_draft=draft,
        asset_policies=[],
    )
    state[PostWorkflowSection.DESIGN_QUALITY.value] = {
        "decision": "PASS",
        "render_fingerprint": design_payload.post_draft.render_fingerprint,
    }
    return state, storage, payload


@pytest.mark.asyncio
async def test_clean_render_passes_after_all_ticket_dimensions_are_assessed() -> None:
    _, _, payload = await _payload()
    vision = _Vision(_readout())

    report = await VisionCritic(vision).review(payload)

    assert report.decision is VisionCriticDecision.PASS
    assert report.assessed_dimensions == list(VisionDimension)
    assert report.issues == []
    assert report.render_checksum == payload.post_draft.final_asset.checksum
    assert "actual pixels" in vision.requests[0].prompt
    assert "EXPECTED CONTEXT" in vision.requests[0].prompt


@pytest.mark.asyncio
async def test_perceptual_failure_preserves_expected_observed_region_and_confidence() -> None:
    _, _, payload = await _payload()

    report = await VisionCritic(
        _Vision(_readout(failing=VisionDimension.VISUAL_HIERARCHY))
    ).review(payload)

    assert report.decision is VisionCriticDecision.REVISE
    issue = report.issues[0]
    assert issue.region == "center product and upper headline"
    assert issue.confidence == 0.96
    assert "Headline" in issue.expected
    assert "Product" in issue.observed


@pytest.mark.asyncio
async def test_invalid_provider_output_gets_one_repair_then_fails_closed() -> None:
    _, _, payload = await _payload()
    vision = _Vision({}, _readout())
    assert (await VisionCritic(vision).review(payload)).decision is VisionCriticDecision.PASS
    assert len(vision.requests) == 2
    assert "CORRECTION PASS" in vision.requests[1].prompt

    with pytest.raises(ProviderResponseError):
        await VisionCritic(_Vision({}, {"still": "invalid"})).review(payload)


@pytest.mark.asyncio
async def test_render_checksum_mismatch_is_rejected_before_provider_use() -> None:
    _, _, payload = await _payload()
    with pytest.raises(ValidationError, match="checksum"):
        VisionCriticInput(**{**payload.model_dump(), "final_image": b"different render"})


@pytest.mark.asyncio
async def test_stage_routes_hierarchy_failure_to_smallest_layout_revision() -> None:
    state, storage, _ = await _payload()
    handler = VisionCriticStageHandler(
        _providers(_Vision(_readout(failing=VisionDimension.VISUAL_HIERARCHY)), storage)
    )

    result = await handler.execute(_context(state))

    report = result.outputs[PostWorkflowSection.VISION_QUALITY]
    history = result.outputs[PostWorkflowSection.REVISION_HISTORY]
    assert report["decision"] == "REVISE"
    assert history[-1]["requested_by"] == SupervisorStage.VISION_REVIEW.value
    assert history[-1]["route"] == "layout"
    assert history[-1]["findings"][0]["location"] == "center product and upper headline"


def test_supervisor_places_vision_review_before_quality_scoring() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.VISION_REVIEW)
    assert policy.dependencies == (SupervisorStage.DESIGN_REVIEW,)
    assert PostWorkflowSection.VISION_QUALITY in policy.output_sections
    scoring = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.QUALITY_SCORING)
    assert scoring.dependencies == (SupervisorStage.VISION_REVIEW,)
    assert PostWorkflowSection.VISION_QUALITY in scoring.required_sections
