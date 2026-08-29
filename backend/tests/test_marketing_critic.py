from typing import Any

import pytest
from test_composition_stage import _context as _composition_context
from test_composition_stage import _fixture as _composition_fixture
from test_copywriter_agent import _CopyLLM
from test_copywriter_agent import _input as _copy_input
from test_copywriter_agent import _run as _run_copy

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
)
from app.modules.posts.agents.marketing_critic import (
    MARKETING_PASS_SCORE,
    MarketingCriticAgent,
    MarketingCriticDecision,
    MarketingCriticInput,
    MarketingDimension,
    MarketingDimensionReview,
    MarketingIssueSeverity,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import MarketingCriticStageHandler
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
        data = self.responses.pop(0) if self.responses else {}
        return VisionResponse(data=data, provider="test-vision", model="marketing-critic-test")


def _readout(
    *, failing: MarketingDimension | None = None, severity: str = "high"
) -> dict[str, Any]:
    reviews = []
    for dimension in MarketingDimension:
        failed = dimension is failing
        reviews.append(
            {
                "dimension": dimension.value,
                "score": 5 if failed else MARKETING_PASS_SCORE,
                "issue": "The draft obscures the approved marketing decision." if failed else None,
                "severity": severity if failed else None,
                "reason": (
                    "The visible message does not express the approved strategic promise."
                    if failed
                    else "The visible draft is consistent with the approved context."
                ),
                "recommended_action": (
                    "Replace the conflicting message with one expression of the approved promise."
                    if failed
                    else None
                ),
            }
        )
    return {"reviews": reviews, "summary": "The review names strengths and actionable gaps."}


async def _state() -> tuple[dict[str, Any], Any, MarketingCriticInput]:
    copy_input = await _copy_input()
    copy = await _run_copy(copy_input, _CopyLLM())
    composition = await _composition_fixture()
    composed = await composition.handler().execute(_composition_context(composition.state))
    draft = PostDraft.model_validate(composed.outputs[PostWorkflowSection.POST_DRAFT])
    draft = draft.model_copy(update={"contract_fingerprint": copy.contract_fingerprint})
    image = await composition.storage.get(key=draft.final_asset.storage_key)
    payload = MarketingCriticInput(
        final_image=image,
        final_mime_type=draft.final_asset.mime_type,
        semantic_contract=copy_input.semantic_contract,
        strategy=copy_input.strategy,
        copy_draft=copy,
        post_draft=draft,
    )
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = copy_input.semantic_contract
    state[PostWorkflowSection.MARKETING_STRATEGY.value] = copy_input.strategy.model_dump(
        mode="json"
    )
    state[PostWorkflowSection.BRAND.value] = {"available": True}
    state[PostWorkflowSection.CREATIVE_CONCEPT.value] = {"available": True}
    state[PostWorkflowSection.COPY.value] = copy.model_dump(mode="json")
    state[PostWorkflowSection.POST_DRAFT.value] = draft.model_dump(mode="json")
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
async def test_clean_final_draft_passes_all_eight_marketing_dimensions() -> None:
    _, _, payload = await _state()

    report = await MarketingCriticAgent(_Vision(_readout())).review(payload)

    assert report.decision is MarketingCriticDecision.PASS
    assert report.score == MARKETING_PASS_SCORE
    assert report.issues == []
    assert {review.dimension for review in report.reviews} == set(MarketingDimension)


@pytest.mark.asyncio
async def test_copy_failure_produces_a_complete_actionable_issue() -> None:
    _, _, payload = await _state()

    report = await MarketingCriticAgent(
        _Vision(_readout(failing=MarketingDimension.MESSAGE_CLARITY))
    ).review(payload)

    assert report.decision is MarketingCriticDecision.REVISE
    assert report.issues[0].severity is MarketingIssueSeverity.HIGH
    assert report.issues[0].reason
    assert report.issues[0].recommended_action
    assert report.issues[0].target_stage is SupervisorStage.COPYWRITING


@pytest.mark.asyncio
async def test_strategy_failure_routes_to_marketing_strategist() -> None:
    _, _, payload = await _state()

    report = await MarketingCriticAgent(
        _Vision(_readout(failing=MarketingDimension.POSITIONING))
    ).review(payload)

    assert report.issues[0].target_stage is SupervisorStage.MARKETING_STRATEGY


def test_a_low_score_cannot_exist_without_diagnosis_and_action() -> None:
    with pytest.raises(ValueError, match="requires issue, severity and action"):
        MarketingDimensionReview(
            dimension=MarketingDimension.CTA,
            score=4,
            reason="The CTA is passive.",
        )


@pytest.mark.asyncio
async def test_invalid_provider_output_gets_one_complete_repair_pass() -> None:
    _, _, payload = await _state()
    vision = _Vision({"score": 8.4}, _readout())

    report = await MarketingCriticAgent(vision).review(payload)

    assert report.decision is MarketingCriticDecision.PASS
    assert len(vision.requests) == 2
    assert "CORRECTION PASS" in vision.requests[1].prompt


@pytest.mark.asyncio
async def test_twice_invalid_provider_output_fails_closed() -> None:
    _, _, payload = await _state()

    with pytest.raises(ProviderResponseError, match="unusable structured review"):
        await MarketingCriticAgent(_Vision({"score": 8.4}, {"still": "invalid"})).review(payload)


@pytest.mark.asyncio
async def test_final_render_checksum_drift_is_rejected_before_review() -> None:
    _, _, payload = await _state()

    with pytest.raises(ValueError, match="final render bytes disagree"):
        MarketingCriticInput(
            **{
                **payload.model_dump(),
                "final_image": b"different render bytes",
            }
        )


@pytest.mark.asyncio
async def test_stage_writes_quality_and_requests_smallest_revision() -> None:
    state, storage, _ = await _state()
    handler = MarketingCriticStageHandler(
        _providers(_Vision(_readout(failing=MarketingDimension.CTA)), storage)
    )

    result = await handler.execute(_context(state))

    quality = result.outputs[PostWorkflowSection.QUALITY]
    history = result.outputs[PostWorkflowSection.REVISION_HISTORY]
    assert quality["decision"] == MarketingCriticDecision.REVISE.value
    assert history[-1]["target_stage"] == SupervisorStage.COPYWRITING.value
    assert history[-1]["requested_by"] == SupervisorStage.QUALITY_REVIEW.value
    assert history[-1]["change"] == [PostWorkflowSection.COPY.value]
    assert history[-1]["route"] == "copy"
    assert history[-1]["responsible_component"] == "copywriter"
    assert history[-1]["why"] and history[-1]["action"] and history[-1]["keep"]
    assert history[-1]["iteration"] == 1
    routed = {**state, PostWorkflowSection.QUALITY.value: quality}
    routed[PostWorkflowSection.REVISION_HISTORY.value] = history
    decision = PostSupervisor().decide(routed)
    assert decision.action is SupervisorAction.REVISE
    assert decision.next_stage is SupervisorStage.COPYWRITING


@pytest.mark.asyncio
async def test_persistent_failure_stops_after_revision_budget() -> None:
    state, storage, _ = await _state()
    state[PostWorkflowSection.REVISION_HISTORY.value] = [
        {
            "status": "completed",
            "requested_by": SupervisorStage.QUALITY_REVIEW.value,
            "target_stage": SupervisorStage.COPYWRITING.value,
        }
    ] * 2
    handler = MarketingCriticStageHandler(
        _providers(_Vision(_readout(failing=MarketingDimension.CTA)), storage),
        max_revisions=2,
    )

    with pytest.raises(NonRetryableJobError, match="still fails after 2 revisions"):
        await handler.execute(_context(state))


def test_supervisor_declares_every_marketing_critic_input_and_output() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.QUALITY_REVIEW)

    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.MARKETING_STRATEGY,
        PostWorkflowSection.COPY,
        PostWorkflowSection.POST_DRAFT,
    }
    assert set(policy.output_sections) == {
        PostWorkflowSection.QUALITY,
        PostWorkflowSection.REVISION_HISTORY,
    }
