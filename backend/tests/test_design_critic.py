from io import BytesIO
from typing import Any

import pytest
from PIL import Image
from test_composition_stage import _context as _composition_context
from test_composition_stage import _fixture as _composition_fixture
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
)
from app.modules.posts.agents.design_critic import (
    DesignCriticDecision,
    DesignCriticInput,
    DesignDimension,
    DesignDimensionCheck,
    DesignIssueSeverity,
    SeniorDesignCritic,
)
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import DesignCriticStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    ProviderBundle,
    ProviderResponseError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.composition import PostDraft


class _Vision:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        value = (
            self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
            if self.responses
            else {}
        )
        return VisionResponse(data=value, provider="test-vision", model="design-critic-test")


def _readout(*, failing: DesignDimension | None = None, severity: str = "high") -> dict[str, Any]:
    checks = []
    for dimension in DesignDimension:
        failed = dimension is failing
        checks.append(
            {
                "dimension": dimension.value,
                "passed": not failed,
                "problem": "The headline and CTA compete for primary attention."
                if failed
                else None,
                "location": "upper-right headline region" if failed else None,
                "cause": "Similar scale and contrast create two simultaneous focal points."
                if failed
                else None,
                "severity": severity if failed else None,
                "recommended_change": "Reduce CTA contrast and preserve the headline as primary."
                if failed
                else None,
                "evidence": "The rendered relationships visibly satisfy this design dimension."
                if not failed
                else "Headline and CTA carry nearly equal visual weight.",
            }
        )
    return {"checks": checks, "summary": "A precise design diagnosis of the final render."}


async def _state() -> tuple[dict[str, Any], Any, DesignCriticInput]:
    design_input = await _design_input()
    fingerprint = design_input.copy_draft.contract_fingerprint
    design_spec = DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint)
    composition = await _composition_fixture()
    composed = await composition.handler().execute(_composition_context(composition.state))
    draft = PostDraft.model_validate(composed.outputs[PostWorkflowSection.POST_DRAFT])
    draft = draft.model_copy(update={"contract_fingerprint": fingerprint})
    image = await composition.storage.get(key=draft.final_asset.storage_key)
    payload = DesignCriticInput(
        final_image=image,
        final_mime_type=draft.final_asset.mime_type,
        semantic_contract=design_input.semantic_contract,
        art_direction=design_input.art_direction,
        design_spec=design_spec,
        post_draft=draft,
    )
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = design_input.semantic_contract
    state[PostWorkflowSection.ART_DIRECTION.value] = design_input.art_direction.model_dump(
        mode="json"
    )
    state[PostWorkflowSection.DESIGN_SPEC.value] = design_spec.model_dump(mode="json")
    state[PostWorkflowSection.POST_DRAFT.value] = draft.model_dump(mode="json")
    state[PostWorkflowSection.QUALITY.value] = {"decision": "PASS"}
    state[PostWorkflowSection.BRAND.value] = {"available": True}
    state[PostWorkflowSection.CREATIVE_CONCEPT.value] = {"available": True}
    state[PostWorkflowSection.COPY.value] = design_input.copy_draft.model_dump(mode="json")
    return state, composition.storage, payload


def _providers(vision: _Vision, storage) -> ProviderBundle:
    return ProviderBundle(
        llm=MockLLMProvider(),
        vision=vision,
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=storage,
    )


def _context(state: dict[str, Any]) -> SupervisorStageContext:
    base = _composition_context(state)
    return SupervisorStageContext(
        generation_id=base.generation_id,
        post_id=base.post_id,
        job_id=base.job_id,
        workflow_state=state,
        state_version=base.state_version,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_clean_render_passes_all_sixteen_design_checks_without_scoring() -> None:
    _, _, payload = await _state()
    vision = _Vision(_readout())

    report = await SeniorDesignCritic(vision).review(payload)

    assert report.decision is DesignCriticDecision.PASS
    assert report.problems == []
    assert {check.dimension for check in report.checks} == set(DesignDimension)
    assert "score" not in report.model_dump()
    with Image.open(BytesIO(vision.requests[0].image)) as preview:
        assert max(preview.size) == 640


@pytest.mark.asyncio
async def test_review_is_one_constrained_call_that_cannot_skip_a_diagnosis() -> None:
    _, _, payload = await _state()
    vision = _Vision(_readout())

    await SeniorDesignCritic(vision).review(payload)

    assert len(vision.requests) == 1
    checks = vision.requests[0].response_schema["properties"]["checks"]
    assert checks["minItems"] == checks["maxItems"] == len(DesignDimension)
    failing = next(
        variant
        for variant in checks["items"]["anyOf"]
        if variant["properties"]["passed"]["const"] is False
    )
    assert set(failing["required"]) == {
        "dimension",
        "passed",
        "problem",
        "location",
        "cause",
        "severity",
        "recommended_change",
        "evidence",
    }


@pytest.mark.asyncio
async def test_report_keeps_the_declared_dimension_order() -> None:
    _, _, payload = await _state()
    shuffled = _readout()
    shuffled["checks"] = list(reversed(shuffled["checks"]))

    report = await SeniorDesignCritic(_Vision(shuffled)).review(payload)

    assert [check.dimension for check in report.checks] == list(DesignDimension)


@pytest.mark.asyncio
async def test_failure_is_a_complete_design_diagnosis_not_an_aesthetic_opinion() -> None:
    _, _, payload = await _state()

    report = await SeniorDesignCritic(_Vision(_readout(failing=DesignDimension.HIERARCHY))).review(
        payload
    )

    problem = report.problems[0]
    assert report.decision is DesignCriticDecision.REVISE
    assert problem.problem
    assert problem.location == "upper-right headline region"
    assert problem.cause
    assert problem.severity is DesignIssueSeverity.HIGH
    assert problem.recommended_change
    assert problem.target_stage is SupervisorStage.DESIGN_SPEC


@pytest.mark.parametrize(
    ("dimension", "target"),
    [
        (DesignDimension.CREATIVITY, SupervisorStage.CREATIVE_CONCEPT),
        (DesignDimension.BRAND_CONSISTENCY, SupervisorStage.ART_DIRECTION),
        (DesignDimension.TYPOGRAPHY, SupervisorStage.DESIGN_SPEC),
    ],
)
@pytest.mark.asyncio
async def test_defects_route_to_the_smallest_responsible_stage(
    dimension: DesignDimension, target: SupervisorStage
) -> None:
    _, _, payload = await _state()

    report = await SeniorDesignCritic(_Vision(_readout(failing=dimension))).review(payload)

    assert report.problems[0].target_stage is target


def test_failed_check_cannot_omit_location_cause_or_recommended_change() -> None:
    with pytest.raises(ValueError, match="requires problem, location, cause, severity and change"):
        DesignDimensionCheck(
            dimension=DesignDimension.SPACING,
            passed=False,
            problem="Spacing is inconsistent.",
            evidence="The gaps differ visibly.",
        )


@pytest.mark.asyncio
async def test_invalid_output_gets_one_repair_then_fails_closed() -> None:
    _, _, payload = await _state()
    repaired = _Vision({"aesthetic_score": 9}, _readout())

    report = await SeniorDesignCritic(repaired).review(payload)

    assert report.decision is DesignCriticDecision.PASS
    assert len(repaired.requests) == 2
    assert "CORRECTION PASS" in repaired.requests[1].prompt

    with pytest.raises(ProviderResponseError, match="unusable structured review"):
        await SeniorDesignCritic(_Vision({}, {"still": "invalid"})).review(payload)


@pytest.mark.asyncio
async def test_checksum_drift_is_rejected_before_provider_call() -> None:
    _, _, payload = await _state()

    with pytest.raises(ValueError, match="final render bytes disagree"):
        DesignCriticInput(**{**payload.model_dump(), "final_image": b"wrong render"})


@pytest.mark.asyncio
async def test_stage_persists_report_and_routes_targeted_revision() -> None:
    state, storage, _ = await _state()
    handler = DesignCriticStageHandler(
        _providers(_Vision(_readout(failing=DesignDimension.CONTRAST)), storage)
    )

    result = await handler.execute(_context(state))

    report = result.outputs[PostWorkflowSection.DESIGN_QUALITY]
    history = result.outputs[PostWorkflowSection.REVISION_HISTORY]
    assert report["decision"] == DesignCriticDecision.REVISE.value
    assert history[-1]["target_stage"] == SupervisorStage.DESIGN_SPEC.value
    assert history[-1]["change"] == [PostWorkflowSection.DESIGN_SPEC.value]
    state[PostWorkflowSection.DESIGN_QUALITY.value] = report
    state[PostWorkflowSection.REVISION_HISTORY.value] = history
    decision = PostSupervisor().decide(state)
    assert decision.action is SupervisorAction.REVISE
    assert decision.next_stage is SupervisorStage.DESIGN_SPEC


@pytest.mark.asyncio
async def test_persistent_design_failure_stops_after_revision_budget() -> None:
    state, storage, _ = await _state()
    state[PostWorkflowSection.REVISION_HISTORY.value] = [
        {
            "status": "completed",
            "requested_by": SupervisorStage.DESIGN_REVIEW.value,
            "target_stage": SupervisorStage.DESIGN_SPEC.value,
        }
    ] * 2
    handler = DesignCriticStageHandler(
        _providers(_Vision(_readout(failing=DesignDimension.POLISH)), storage),
        max_revisions=2,
    )

    with pytest.raises(NonRetryableJobError, match="still fails after 2 revisions"):
        await handler.execute(_context(state))


def test_supervisor_places_design_review_after_marketing_review() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.DESIGN_REVIEW)

    assert policy.dependencies == (SupervisorStage.QUALITY_REVIEW,)
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.ART_DIRECTION,
        PostWorkflowSection.DESIGN_SPEC,
        PostWorkflowSection.POST_DRAFT,
        PostWorkflowSection.QUALITY,
    }
    assert set(policy.output_sections) == {
        PostWorkflowSection.DESIGN_QUALITY,
        PostWorkflowSection.REVISION_HISTORY,
    }
