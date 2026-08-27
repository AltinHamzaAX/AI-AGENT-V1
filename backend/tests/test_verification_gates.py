from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
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
from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import VerificationStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    ProviderBundle,
    ProviderResponseError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.composition import ComponentKind, PostDraft
from app.modules.posts.tools.verification import (
    HardVerificationGate,
    RenderReadout,
    VerificationDecision,
    VerificationGate,
    VerificationInput,
    VerificationReport,
    decide_verification,
)


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
        return VisionResponse(data=value, provider="test-vision", model="verification-test")


@dataclass(frozen=True, slots=True)
class _Case:
    payload: VerificationInput
    contract: PostSemanticContract
    storage: Any
    policies: dict[IntelligentAssetRole, AssetPolicy]


def _contract(**overrides: Any) -> PostSemanticContract:
    """A contract the approved copy actually satisfies, so a clean post can pass.

    Every required fact here is stated by the fixture's copy, which is what the
    copywriter is contractually bound to do; the failing cases override it.
    """
    values: dict[str, Any] = {
        "company": "Promotiva Mobility",
        "brand": "Prishtina Drive",
        "product": "Airport car rental",
        "primary_entity": "Airport car rental",
        "goal": "Drive bookings",
        "audience": "Diaspora arriving in Kosovo",
        "market": "Kosovo",
        "location": "Prishtina airport",
        "offer": "From EUR 35/day",
        "cta_intent": "Book now",
        "platform": "Instagram",
        "language": "Albanian",
        "required_facts": {"price": "EUR 35", "arrival promise": "journey starts at arrival"},
        "forbidden_claims": ["cheapest rental in Kosovo"],
        "required_assets": [],
        "constraints": ["Do not replace the product or logo"],
    }
    values.update(overrides)
    return PostSemanticContract.create(**values)


async def _case(*, contract: PostSemanticContract | None = None) -> _Case:
    design_input = await _design_input()
    composition = await _composition_fixture()
    composed = await composition.handler().execute(_composition_context(composition.state))
    draft = PostDraft.model_validate(composed.outputs[PostWorkflowSection.POST_DRAFT])
    image = await composition.storage.get(key=draft.final_asset.storage_key)
    policies = {
        policy.role: policy
        for policy in (
            AssetPolicy.model_validate(item)
            for item in composition.state[PostWorkflowSection.ASSETS.value]
        )
    }
    # The fixture's contract carries a random required asset, so the tests build
    # their own and align every fingerprint to it.
    contract = contract or _contract(
        required_assets=[policies[IntelligentAssetRole.PRIMARY_PRODUCT].asset_id]
    )
    fingerprint = contract.fingerprint
    aligned = {"contract_fingerprint": fingerprint}
    payload = VerificationInput(
        final_image=image,
        final_mime_type=draft.final_asset.mime_type,
        semantic_contract=contract.to_dict(),
        copy_draft=design_input.copy_draft.model_copy(update=aligned),
        design_spec=DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint),
        post_draft=draft.model_copy(update=aligned),
        asset_policies=[policy.model_copy(update=aligned) for policy in policies.values()],
    )
    return _Case(
        payload=payload,
        contract=contract,
        storage=composition.storage,
        policies={role: policy.model_copy(update=aligned) for role, policy in policies.items()},
    )


def _readout(**overrides: Any) -> RenderReadout:
    values: dict[str, Any] = {
        "visible_text": ["Your journey starts at arrival", "Book your drive", "From EUR 35/day"],
        "visible_brands": [],
        "depicted_products": [],
        "description": "A finished car rental post.",
    }
    values.update(overrides)
    return RenderReadout(**values)


def _failed(assessment) -> set[VerificationGate]:
    return {failure.gate for failure in assessment.failures}


def _replace_component(payload: VerificationInput, kind: ComponentKind, **changes: Any):
    components = [
        component.model_copy(update=changes) if component.kind is kind else component
        for component in payload.post_draft.components
    ]
    return payload.post_draft.model_copy(update={"components": components})


def _providers(vision: _Vision, storage) -> ProviderBundle:
    return ProviderBundle(
        llm=MockLLMProvider(),
        vision=vision,
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=storage,
    )


def _state(case: _Case) -> dict[str, Any]:
    payload = case.payload
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = payload.semantic_contract
    state[PostWorkflowSection.COPY.value] = payload.copy_draft.model_dump(mode="json")
    state[PostWorkflowSection.DESIGN_SPEC.value] = payload.design_spec.model_dump(mode="json")
    state[PostWorkflowSection.POST_DRAFT.value] = payload.post_draft.model_dump(mode="json")
    state[PostWorkflowSection.ASSETS.value] = [
        policy.model_dump(mode="json") for policy in payload.asset_policies
    ]
    return state


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
async def test_a_contract_abiding_render_passes_every_hard_gate() -> None:
    case = await _case()

    assessment = decide_verification(_readout(), payload=case.payload)

    assert assessment.decision is VerificationDecision.PASS
    assert assessment.failures == ()
    assert {check.gate for check in assessment.checks} == set(VerificationGate)
    assert all(check.passed for check in assessment.checks)


@pytest.mark.asyncio
async def test_gates_are_reported_for_every_declared_gate_exactly_once() -> None:
    case = await _case()

    assessment = decide_verification(_readout(), payload=case.payload)

    gates = [check.gate for check in assessment.checks]
    assert len(gates) == len(VerificationGate) == 12
    assert len(set(gates)) == len(gates)


@pytest.mark.asyncio
async def test_export_that_is_not_a_whole_multiple_of_the_canvas_is_blocked() -> None:
    case = await _case()
    final = case.payload.post_draft.final_asset.model_copy(update={"width": 2000})
    payload = case.payload.model_copy(
        update={"post_draft": case.payload.post_draft.model_copy(update={"final_asset": final})}
    )

    assessment = decide_verification(_readout(), payload=payload)

    assert VerificationGate.CORRECT_DIMENSIONS in _failed(assessment)
    assert assessment.decision is VerificationDecision.BLOCKED


@pytest.mark.asyncio
async def test_export_that_changes_the_aspect_ratio_is_blocked() -> None:
    case = await _case()
    canvas = case.payload.design_spec.canvas
    final = case.payload.post_draft.final_asset.model_copy(
        update={"width": canvas.width * 2, "height": canvas.height * 3}
    )
    payload = case.payload.model_copy(
        update={"post_draft": case.payload.post_draft.model_copy(update={"final_asset": final})}
    )

    assessment = decide_verification(_readout(), payload=payload)

    assert VerificationGate.CORRECT_DIMENSIONS in _failed(assessment)


@pytest.mark.asyncio
async def test_altered_protected_original_fails_asset_fidelity() -> None:
    case = await _case()
    draft = _replace_component(case.payload, ComponentKind.PRODUCT, identity_preserved=False)
    payload = case.payload.model_copy(update={"post_draft": draft})

    assessment = decide_verification(_readout(), payload=payload)

    assert VerificationGate.ASSET_FIDELITY in _failed(assessment)
    assert assessment.decision is VerificationDecision.BLOCKED


@pytest.mark.asyncio
async def test_required_asset_that_never_reached_the_render_is_blocked() -> None:
    case = await _case(contract=_contract(required_assets=[uuid4()]))

    assessment = decide_verification(_readout(), payload=case.payload)

    assert VerificationGate.REQUIRED_ASSETS_PRESENT in _failed(assessment)


@pytest.mark.asyncio
async def test_logo_region_carrying_an_unapproved_asset_is_blocked() -> None:
    case = await _case()
    draft = _replace_component(case.payload, ComponentKind.LOGO, source_asset_id=uuid4())
    payload = case.payload.model_copy(update={"post_draft": draft})

    assessment = decide_verification(_readout(), payload=payload)

    assert VerificationGate.CORRECT_LOGO in _failed(assessment)


@pytest.mark.asyncio
async def test_rendered_text_that_drifted_from_the_approved_copy_is_blocked() -> None:
    """A truncated headline is the failure this gate exists for."""
    case = await _case()
    draft = _replace_component(
        case.payload, ComponentKind.TYPOGRAPHY, text="Your journey starts at arriv..."
    )
    payload = case.payload.model_copy(update={"post_draft": draft})

    assessment = decide_verification(_readout(), payload=payload)

    failure = next(
        item for item in assessment.failures if item.gate is VerificationGate.CORRECT_SPELLING
    )
    assert "Your journey starts at arriv..." in failure.evidence
    assert assessment.decision is VerificationDecision.BLOCKED


@pytest.mark.asyncio
async def test_offer_whose_figures_never_reach_the_copy_is_blocked() -> None:
    case = await _case(
        contract=_contract(
            offer="From EUR 99/day",
            required_facts={"arrival promise": "journey starts at arrival"},
        )
    )

    assessment = decide_verification(_readout(), payload=case.payload)

    failure = next(
        item for item in assessment.failures if item.gate is VerificationGate.CORRECT_OFFER
    )
    assert "99" in failure.evidence


@pytest.mark.asyncio
async def test_missing_required_fact_is_blocked() -> None:
    case = await _case(contract=_contract(required_facts={"fleet size": "480 vehicles nationwide"}))

    assessment = decide_verification(_readout(), payload=case.payload)

    assert VerificationGate.REQUIRED_FACTS_PRESENT in _failed(assessment)


@pytest.mark.asyncio
async def test_forbidden_claim_in_the_published_copy_is_blocked() -> None:
    case = await _case()
    copy = case.payload.copy_draft.model_copy(
        update={"caption": "The cheapest rental in Kosovo, guaranteed."}
    )
    payload = case.payload.model_copy(update={"copy_draft": copy})

    assessment = decide_verification(_readout(), payload=payload)

    failure = next(
        item
        for item in assessment.failures
        if item.gate is VerificationGate.FORBIDDEN_CLAIMS_ABSENT
    )
    assert failure.evidence == ["cheapest rental in Kosovo"]


@pytest.mark.asyncio
async def test_foreign_brand_visible_in_the_render_is_blocked() -> None:
    case = await _case()

    assessment = decide_verification(_readout(visible_brands=["Hertz"]), payload=case.payload)

    assert VerificationGate.CORRECT_BRAND in _failed(assessment)


@pytest.mark.asyncio
async def test_brand_mark_without_a_composited_logo_is_fake_branding() -> None:
    case = await _case()
    draft = case.payload.post_draft.model_copy(
        update={
            "components": [
                component
                for component in case.payload.post_draft.components
                if component.kind is not ComponentKind.LOGO
            ]
        }
    )
    payload = case.payload.model_copy(update={"post_draft": draft})

    assessment = decide_verification(_readout(visible_brands=["Prishtina Drive"]), payload=payload)

    assert VerificationGate.FAKE_BRANDING_ABSENT in _failed(assessment)


@pytest.mark.asyncio
async def test_legible_text_that_belongs_to_no_approved_copy_is_blocked() -> None:
    case = await _case()

    assessment = decide_verification(
        _readout(visible_text=["Your journey starts at arrival", "MEGA SALE 70% OFF"]),
        payload=case.payload,
    )

    failure = next(
        item for item in assessment.failures if item.gate is VerificationGate.UNWANTED_TEXT_ABSENT
    )
    assert failure.evidence == ["MEGA SALE 70% OFF"]


@pytest.mark.asyncio
async def test_misread_approved_copy_does_not_block_the_post() -> None:
    """A small vision model mangles glyphs; that is not evidence of other text."""
    case = await _case()

    assessment = decide_verification(
        _readout(visible_text=["Your journcy starts at arrival"]), payload=case.payload
    )

    assert VerificationGate.UNWANTED_TEXT_ABSENT not in _failed(assessment)


@pytest.mark.asyncio
async def test_render_depicting_another_subject_is_blocked() -> None:
    case = await _case()

    assessment = decide_verification(
        _readout(depicted_products=["espresso machine"]), payload=case.payload
    )

    assert VerificationGate.CORRECT_PRODUCT in _failed(assessment)


@pytest.mark.asyncio
async def test_product_region_carrying_an_unapproved_asset_is_blocked() -> None:
    case = await _case()
    draft = _replace_component(case.payload, ComponentKind.PRODUCT, source_asset_id=uuid4())
    payload = case.payload.model_copy(update={"post_draft": draft})

    assessment = decide_verification(_readout(), payload=payload)

    assert VerificationGate.CORRECT_PRODUCT in _failed(assessment)


@pytest.mark.asyncio
async def test_checksum_drift_is_rejected_before_any_gate_runs() -> None:
    case = await _case()

    with pytest.raises(ValueError, match="final render bytes disagree"):
        VerificationInput(**{**case.payload.model_dump(), "final_image": b"other render"})


@pytest.mark.asyncio
async def test_verification_is_one_constrained_call_and_fails_closed() -> None:
    case = await _case()
    vision = _Vision(_readout().model_dump(mode="json"))

    report = await HardVerificationGate(vision).verify(case.payload)

    assert report.decision is VerificationDecision.PASS
    assert len(vision.requests) == 1
    schema = vision.requests[0].response_schema
    assert set(schema["required"]) == {
        "visible_text",
        "visible_brands",
        "depicted_products",
        "description",
    }

    with pytest.raises(ProviderResponseError, match="unusable render readout"):
        await HardVerificationGate(_Vision({}, {"still": "invalid"})).verify(case.payload)


@pytest.mark.asyncio
async def test_unusable_readout_never_certifies_and_never_blocks() -> None:
    """Failing to look at a post is not evidence against it, so it raises."""
    case = await _case()
    vision = _Vision({"visible_text": "not a list"}, _readout().model_dump(mode="json"))

    report = await HardVerificationGate(vision).verify(case.payload)

    assert report.decision is VerificationDecision.PASS
    assert len(vision.requests) == 2


def test_report_cannot_pass_while_a_gate_failed() -> None:
    checks = [
        {"gate": gate.value, "passed": gate is not VerificationGate.CORRECT_LOGO, "detail": "d"}
        for gate in VerificationGate
    ]
    with pytest.raises(ValueError, match="failures disagree with failed gates"):
        VerificationReport(
            decision=VerificationDecision.BLOCKED,
            checks=checks,
            failures=[],
            reason="r",
            render_checksum="a" * 64,
            render_fingerprint="b" * 64,
            contract_fingerprint="c" * 64,
        )


def test_report_cannot_claim_pass_with_a_recorded_failure() -> None:
    checks = [
        {"gate": gate.value, "passed": gate is not VerificationGate.CORRECT_LOGO, "detail": "d"}
        for gate in VerificationGate
    ]
    with pytest.raises(ValueError, match="decision disagrees with its gates"):
        VerificationReport(
            decision=VerificationDecision.PASS,
            checks=checks,
            failures=[{"gate": VerificationGate.CORRECT_LOGO.value, "detail": "d"}],
            reason="r",
            render_checksum="a" * 64,
            render_fingerprint="b" * 64,
            contract_fingerprint="c" * 64,
        )


@pytest.mark.asyncio
async def test_stage_persists_the_report_without_requesting_a_revision() -> None:
    case = await _case()
    state = _state(case)
    handler = VerificationStageHandler(
        _providers(_Vision(_readout().model_dump(mode="json")), case.storage)
    )

    result = await handler.execute(_context(state))

    assert set(result.outputs) == {PostWorkflowSection.VERIFICATION}
    report = VerificationReport.model_validate(result.outputs[PostWorkflowSection.VERIFICATION])
    assert report.decision is VerificationDecision.PASS
    assert report.certifies(case.payload.post_draft.final_asset.checksum)


@pytest.mark.asyncio
async def test_blocked_verification_stops_the_workflow_at_a_perfect_score() -> None:
    """The ticket's whole point: a hard gate is not weighed against the scores."""
    case = await _case()
    state = _state(case)
    readout = _readout(visible_brands=["Hertz"]).model_dump(mode="json")
    handler = VerificationStageHandler(_providers(_Vision(readout), case.storage))

    result = await handler.execute(_context(state))
    state[PostWorkflowSection.VERIFICATION.value] = result.outputs[PostWorkflowSection.VERIFICATION]
    state[PostWorkflowSection.QUALITY.value] = {"decision": "PASS", "score": 9.8}
    state[PostWorkflowSection.DESIGN_QUALITY.value] = {"decision": "PASS"}
    state[PostWorkflowSection.QUALITY_APPROVAL.value] = {"decision": "PASS"}

    decision = PostSupervisor().decide(state)

    assert state[PostWorkflowSection.VERIFICATION.value]["decision"] == "BLOCKED"
    assert decision.terminal is True
    assert decision.next_stage is None
    assert decision.reason == "hard verification gate blocked the workflow"


def _finished_state() -> dict[str, Any]:
    """Every stage run, so `decide` reaches the completion chain."""
    state = empty_workflow_state()
    state[PostWorkflowSection.SUPERVISOR.value] = {
        "current_stage": None,
        "completed_stages": [stage.value for stage in SupervisorStage],
        "skipped_stages": [],
        "invalidated_stages": [],
        "requested_skips": [],
        "stage_attempts": {},
        "last_decision": {},
    }
    return state


def test_completion_asks_for_the_gates_before_it_asks_for_a_review() -> None:
    state = _finished_state()
    state[PostWorkflowSection.QUALITY.value] = {"decision": "PASS", "score": 9.8}
    state[PostWorkflowSection.DESIGN_QUALITY.value] = {"decision": "PASS"}

    decision = PostSupervisor().decide(state)

    assert decision.next_stage is SupervisorStage.VERIFICATION
    assert decision.terminal is False
    assert decision.reason == "every hard verification gate must pass before completion"


def test_workflow_completes_only_when_gates_and_reviews_all_pass() -> None:
    state = _finished_state()
    state[PostWorkflowSection.VERIFICATION.value] = {"decision": "PASS"}
    state[PostWorkflowSection.QUALITY.value] = {"decision": "PASS"}
    state[PostWorkflowSection.DESIGN_QUALITY.value] = {"decision": "PASS"}
    state[PostWorkflowSection.QUALITY_APPROVAL.value] = {"decision": "PASS"}

    decision = PostSupervisor().decide(state)

    assert decision.terminal is True
    assert decision.next_stage is None
    assert PostWorkflowSection.VERIFICATION in decision.state_requirements


def test_supervisor_runs_hard_gates_before_anything_scores_the_post() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.VERIFICATION)
    quality = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.QUALITY_REVIEW)

    assert policy.dependencies == (SupervisorStage.COMPOSITION,)
    assert quality.dependencies == (SupervisorStage.VERIFICATION,)
    # A hard gate that could ask for a revision would be a score with extra steps.
    assert policy.output_sections == (PostWorkflowSection.VERIFICATION,)
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.COPY,
        PostWorkflowSection.DESIGN_SPEC,
        PostWorkflowSection.POST_DRAFT,
    }
