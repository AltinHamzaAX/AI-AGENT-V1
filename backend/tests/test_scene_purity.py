import hashlib
from typing import Any
from uuid import uuid4

import pytest
from test_deterministic_composer import _png
from test_generation_planner import _asset
from test_image_prompt_and_scene_generation import _fixture as _scene_fixture

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import ScenePurityStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    ProviderBundle,
    ProviderResponseError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.generation import (
    GenerationKind,
    GenerationPlanner,
    GenerationPlannerInput,
    SceneArtifact,
    SceneGenerationStatus,
)
from app.modules.posts.tools.scene_purity import (
    CLEAN_DETAIL,
    CONFIDENCE_THRESHOLD,
    ContaminationKind,
    ScenePurityCheck,
    ScenePurityInput,
    ScenePurityInspector,
    ScenePurityReport,
    ScenePurityVerdict,
    SceneReadout,
    decide_scene_purity,
)

SCENE_KEY = "posts/scene/plate.png"


class _StubVision:
    """Returns canned readouts so the policy is what the assertions exercise."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        data = self.payloads.pop(0) if self.payloads else {}
        return VisionResponse(data=data, provider="test-vision", model="purity-test")


def _readout(**overrides: Any) -> dict[str, Any]:
    """A plate the model reports as entirely clean, before any override."""
    confidences: dict[ContaminationKind, float] = overrides.pop("confidences", {})
    payload: dict[str, Any] = {
        "observations": [
            {
                "kind": kind.value,
                "confidence": confidences.get(kind, 0.0),
                "evidence": f"nothing resembling {kind.value} is visible",
            }
            for kind in ContaminationKind
        ],
        "visible_text": [],
        "visible_brands": [],
        "depicted_products": [],
        "description": "An empty airport forecourt at dawn with soft directional light.",
    }
    payload.update(overrides)
    return payload


async def _payload(*, protected: bool = False, image: bytes | None = None) -> ScenePurityInput:
    prompt_input, _ = await _scene_fixture()
    fingerprint = prompt_input.design_spec.contract_fingerprint
    policies = (
        [
            _asset(IntelligentAssetRole.PRIMARY_PRODUCT, fingerprint),
            _asset(IntelligentAssetRole.BRAND_LOGO, fingerprint),
        ]
        if protected
        else []
    )
    plan = GenerationPlanner().build(
        GenerationPlannerInput(
            assets=policies,
            design_spec=prompt_input.design_spec,
            semantic_contract=prompt_input.semantic_contract,
        )
    )
    data = image if image is not None else _png((1080, 1080), (18, 42, 74, 255))
    return ScenePurityInput(
        scene_image=data,
        scene_mime_type="image/png",
        scene_checksum=hashlib.sha256(data).hexdigest(),
        scene_storage_key=SCENE_KEY,
        semantic_contract=prompt_input.semantic_contract,
        generation_plan=plan,
        asset_policies=policies,
    )


def _clean_report(*, checksum: str, storage_key: str, contract_fingerprint: str) -> dict[str, Any]:
    """A PASS report certifying exactly these bytes, for downstream stage tests."""
    return ScenePurityReport(
        verdict=ScenePurityVerdict.PASS,
        inspected=True,
        checks=[
            ScenePurityCheck(kind=kind, passed=True, detail=CLEAN_DETAIL[kind])
            for kind in ContaminationKind
        ],
        scene_checksum=checksum,
        scene_storage_key=storage_key,
        reason="Generated scene is clean; every contamination check passed.",
        provider="test-vision",
        model="purity-test",
        contract_fingerprint=contract_fingerprint,
    ).model_dump(mode="json")


def _decide(payload: ScenePurityInput, **overrides: Any):
    return decide_scene_purity(SceneReadout.model_validate(_readout(**overrides)), payload=payload)


@pytest.mark.asyncio
async def test_clean_plate_passes_every_contamination_check() -> None:
    payload = await _payload()
    vision = _StubVision(_readout())

    report = await ScenePurityInspector(vision).inspect(payload)

    assert report.verdict is ScenePurityVerdict.PASS
    assert report.inspected is True
    assert {check.kind for check in report.checks} == set(ContaminationKind)
    assert all(check.passed for check in report.checks)
    assert report.findings == []
    assert report.certifies(payload.scene_checksum) is True
    assert len(vision.requests) == 1


@pytest.mark.parametrize("kind", list(ContaminationKind))
@pytest.mark.asyncio
async def test_every_contamination_kind_forces_a_regeneration(kind: ContaminationKind) -> None:
    payload = await _payload()

    assessment = _decide(payload, confidences={kind: CONFIDENCE_THRESHOLD[kind]})

    assert assessment.verdict is ScenePurityVerdict.REGENERATE_SCENE
    assert [finding.kind for finding in assessment.findings] == [kind]
    assert next(check for check in assessment.checks if check.kind is kind).passed is False


@pytest.mark.asyncio
async def test_confidence_below_the_threshold_does_not_block() -> None:
    payload = await _payload()
    kind = ContaminationKind.AI_ARTIFACT

    assessment = _decide(payload, confidences={kind: CONFIDENCE_THRESHOLD[kind] - 0.01})

    assert assessment.verdict is ScenePurityVerdict.PASS


@pytest.mark.asyncio
async def test_transcribed_text_blocks_even_when_the_model_calls_the_plate_clean() -> None:
    payload = await _payload()

    assessment = _decide(payload, visible_text=["SUMMER SALE", "-40%"])

    assert assessment.verdict is ScenePurityVerdict.REGENERATE_SCENE
    finding = next(item for item in assessment.findings if item.kind is ContaminationKind.FAKE_TEXT)
    assert "SUMMER SALE" in finding.detail


@pytest.mark.asyncio
async def test_a_single_stray_glyph_is_treated_as_photographic_noise() -> None:
    payload = await _payload()

    assessment = _decide(payload, visible_text=["x"])

    assert assessment.verdict is ScenePurityVerdict.PASS


@pytest.mark.asyncio
async def test_own_brand_mark_is_a_fake_logo_but_not_an_unexpected_brand() -> None:
    payload = await _payload()

    assessment = _decide(payload, visible_brands=["Prishtina Drive"])

    kinds = {finding.kind for finding in assessment.findings}
    assert kinds == {ContaminationKind.FAKE_LOGO}
    assert assessment.verdict is ScenePurityVerdict.REGENERATE_SCENE


@pytest.mark.asyncio
async def test_third_party_brand_is_both_a_fake_logo_and_an_unexpected_brand() -> None:
    payload = await _payload()

    assessment = _decide(payload, visible_brands=["Hertz"])

    kinds = {finding.kind for finding in assessment.findings}
    assert kinds == {ContaminationKind.FAKE_LOGO, ContaminationKind.UNEXPECTED_BRAND}


@pytest.mark.asyncio
async def test_protected_original_makes_any_depicted_product_wrong() -> None:
    payload = await _payload(protected=True)

    assessment = _decide(payload, depicted_products=["a silver sedan"])

    finding = next(
        item for item in assessment.findings if item.kind is ContaminationKind.WRONG_PRODUCT
    )
    assert "owns the product region" in finding.detail


@pytest.mark.asyncio
async def test_generated_subject_matching_the_contract_is_not_a_wrong_product() -> None:
    payload = await _payload()

    assessment = _decide(payload, depicted_products=["an airport car rental counter"])

    assert assessment.verdict is ScenePurityVerdict.PASS


@pytest.mark.asyncio
async def test_generated_subject_unrelated_to_the_contract_is_a_wrong_product() -> None:
    payload = await _payload()

    assessment = _decide(payload, depicted_products=["an espresso machine"])

    assert [item.kind for item in assessment.findings] == [ContaminationKind.WRONG_PRODUCT]


@pytest.mark.asyncio
async def test_the_same_readout_always_yields_the_same_verdict() -> None:
    payload = await _payload()
    readout = _readout(visible_text=["OPEN"], confidences={ContaminationKind.DISTORTION: 0.9})

    first = decide_scene_purity(SceneReadout.model_validate(readout), payload=payload)
    second = decide_scene_purity(SceneReadout.model_validate(readout), payload=payload)

    assert first == second


@pytest.mark.asyncio
async def test_an_unusable_readout_is_repaired_before_it_is_trusted() -> None:
    payload = await _payload()
    vision = _StubVision({"description": "looks fine to me"}, _readout())

    report = await ScenePurityInspector(vision).inspect(payload)

    assert report.verdict is ScenePurityVerdict.PASS
    assert len(vision.requests) == 2
    assert "CORRECTION PASS" in vision.requests[1].prompt


@pytest.mark.asyncio
async def test_prompt_demonstrates_every_required_observation() -> None:
    payload = await _payload()
    vision = _StubVision(_readout())

    await ScenePurityInspector(vision).inspect(payload)

    prompt = vision.requests[0].prompt
    for kind in ContaminationKind:
        assert prompt.count(f'"kind": "{kind.value}"') == 1
    assert '"visible_text": []' in prompt
    assert '"visible_brands": []' in prompt
    assert '"depicted_products": []' in prompt


@pytest.mark.asyncio
async def test_a_readout_that_cannot_be_repaired_fails_closed() -> None:
    payload = await _payload()
    vision = _StubVision({"description": "nope"}, {"still": "wrong"})

    with pytest.raises(ProviderResponseError, match="unusable readout"):
        await ScenePurityInspector(vision).inspect(payload)


@pytest.mark.asyncio
async def test_a_report_cannot_be_replayed_over_different_bytes() -> None:
    data = _png((1080, 1080), (18, 42, 74, 255))

    with pytest.raises(ValueError, match="disagree with the recorded scene checksum"):
        payload = await _payload(image=data)
        ScenePurityInput(
            **{
                **payload.model_dump(),
                "scene_image": data,
                "scene_checksum": "0" * 64,
            }
        )


@pytest.mark.asyncio
async def test_a_report_verdict_cannot_contradict_its_findings() -> None:
    payload = await _payload()
    assessment = _decide(payload, visible_text=["SALE"])

    with pytest.raises(ValueError, match="verdict disagrees with its findings"):
        ScenePurityReport(
            verdict=ScenePurityVerdict.PASS,
            inspected=True,
            checks=list(assessment.checks),
            findings=list(assessment.findings),
            scene_checksum=payload.scene_checksum,
            scene_storage_key=payload.scene_storage_key,
            reason="forced pass",
            contract_fingerprint=payload.contract().fingerprint,
        )


# --- stage handler -----------------------------------------------------------


def _providers(vision: Any, storage: MockStorageProvider) -> ProviderBundle:
    return ProviderBundle(
        llm=MockLLMProvider(),
        vision=vision,
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=storage,
    )


def _context(state: dict[str, Any]) -> SupervisorStageContext:
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )


async def _stage_state(
    *, generated: bool = True, history: list[Any] | None = None
) -> tuple[dict[str, Any], MockStorageProvider, bytes]:
    _, state = await _scene_fixture()
    data = _png((1080, 1080), (18, 42, 74, 255))
    storage = MockStorageProvider()
    if generated:
        await storage.put(key=SCENE_KEY, data=data, content_type="image/png")
        artifact = SceneArtifact(
            status=SceneGenerationStatus.GENERATED,
            kind=GenerationKind.SCENE,
            storage_key=SCENE_KEY,
            mime_type="image/png",
            width=1080,
            height=1080,
            checksum=hashlib.sha256(data).hexdigest(),
            provider="test-image",
            model="scene-test",
            prompt_fingerprint="a" * 64,
            reason="generated for the scene purity test",
        )
    else:
        artifact = SceneArtifact(
            status=SceneGenerationStatus.SKIPPED,
            kind=None,
            reason="Approved assets already provide the scene; image generation skipped.",
        )
    state[PostWorkflowSection.GENERATION_ARTIFACTS.value] = [artifact.model_dump(mode="json")]
    state[PostWorkflowSection.REVISION_HISTORY.value] = list(history or [])
    return state, storage, data


@pytest.mark.asyncio
async def test_stage_certifies_a_clean_plate_without_touching_revision_history() -> None:
    state, storage, data = await _stage_state()
    handler = ScenePurityStageHandler(_providers(_StubVision(_readout()), storage))

    result = await handler.execute(_context(state))

    report = ScenePurityReport.model_validate(result.outputs[PostWorkflowSection.SCENE_PURITY])
    assert report.verdict is ScenePurityVerdict.PASS
    assert report.certifies(hashlib.sha256(data).hexdigest()) is True
    assert result.outputs[PostWorkflowSection.REVISION_HISTORY] == []


@pytest.mark.asyncio
async def test_contaminated_plate_sends_production_back_for_a_new_scene() -> None:
    state, storage, _ = await _stage_state()
    vision = _StubVision(_readout(visible_text=["BOOK NOW"], visible_brands=["Hertz"]))
    handler = ScenePurityStageHandler(_providers(vision, storage))

    result = await handler.execute(_context(state))

    report = ScenePurityReport.model_validate(result.outputs[PostWorkflowSection.SCENE_PURITY])
    assert report.verdict is ScenePurityVerdict.REGENERATE_SCENE
    history = result.outputs[PostWorkflowSection.REVISION_HISTORY]
    assert len(history) == 1
    assert history[0]["status"] == "pending"
    assert history[0]["target_stage"] == SupervisorStage.PRODUCTION.value
    assert history[0]["requested_by"] == SupervisorStage.SCENE_PURITY.value
    assert set(history[0]["contaminations"]) == {
        ContaminationKind.FAKE_TEXT.value,
        ContaminationKind.FAKE_LOGO.value,
        ContaminationKind.UNEXPECTED_BRAND.value,
    }


@pytest.mark.asyncio
async def test_supervisor_routes_the_request_back_to_production() -> None:
    state, storage, _ = await _stage_state()
    vision = _StubVision(_readout(visible_text=["BOOK NOW"]))
    result = await ScenePurityStageHandler(_providers(vision, storage)).execute(_context(state))
    routed = {
        **empty_workflow_state(),
        PostWorkflowSection.REVISION_HISTORY.value: result.outputs[
            PostWorkflowSection.REVISION_HISTORY
        ],
        PostWorkflowSection.SEMANTIC_CONTRACT.value: state[
            PostWorkflowSection.SEMANTIC_CONTRACT.value
        ],
        PostWorkflowSection.DESIGN_SPEC.value: state[PostWorkflowSection.DESIGN_SPEC.value],
        PostWorkflowSection.GENERATION_PLAN.value: state[PostWorkflowSection.GENERATION_PLAN.value],
        PostWorkflowSection.ART_DIRECTION.value: state[PostWorkflowSection.ART_DIRECTION.value],
        PostWorkflowSection.CREATIVE_CONCEPT.value: state[
            PostWorkflowSection.CREATIVE_CONCEPT.value
        ],
        PostWorkflowSection.GENERATION_ARTIFACTS.value: state[
            PostWorkflowSection.GENERATION_ARTIFACTS.value
        ],
        PostWorkflowSection.ASSETS.value: [
            _asset(
                IntelligentAssetRole.PRIMARY_PRODUCT,
                PostSemanticContract.from_dict(
                    state[PostWorkflowSection.SEMANTIC_CONTRACT.value]
                ).fingerprint,
            ).model_dump(mode="json")
        ],
    }

    decision = PostSupervisor().decide(routed)

    assert decision.action is SupervisorAction.REVISE
    assert decision.next_stage is SupervisorStage.PRODUCTION


@pytest.mark.asyncio
async def test_a_plate_that_stays_dirty_stops_the_workflow() -> None:
    exhausted = [
        {
            "status": "completed",
            "target_stage": SupervisorStage.PRODUCTION.value,
            "requested_by": SupervisorStage.SCENE_PURITY.value,
        }
    ] * 2
    state, storage, _ = await _stage_state(history=exhausted)
    vision = _StubVision(_readout(visible_text=["STILL DIRTY"]))
    handler = ScenePurityStageHandler(_providers(vision, storage), max_regenerations=2)

    with pytest.raises(NonRetryableJobError, match="still fails after 2 regenerations"):
        await handler.execute(_context(state))


@pytest.mark.asyncio
async def test_a_retried_inspection_does_not_stack_duplicate_requests() -> None:
    state, storage, _ = await _stage_state()
    vision = _StubVision(_readout(visible_text=["BOOK NOW"]), _readout(visible_text=["BOOK NOW"]))
    handler = ScenePurityStageHandler(_providers(vision, storage), max_regenerations=3)

    first = await handler.execute(_context(state))
    state[PostWorkflowSection.REVISION_HISTORY.value] = first.outputs[
        PostWorkflowSection.REVISION_HISTORY
    ]
    second = await handler.execute(_context(state))

    assert (
        second.outputs[PostWorkflowSection.REVISION_HISTORY]
        == first.outputs[PostWorkflowSection.REVISION_HISTORY]
    )


@pytest.mark.asyncio
async def test_a_scene_that_was_never_generated_is_not_inspected() -> None:
    state, storage, _ = await _stage_state(generated=False)
    vision = _StubVision()

    result = await ScenePurityStageHandler(_providers(vision, storage)).execute(_context(state))

    report = ScenePurityReport.model_validate(result.outputs[PostWorkflowSection.SCENE_PURITY])
    assert report.inspected is False
    assert report.verdict is ScenePurityVerdict.PASS
    assert report.scene_checksum is None
    assert vision.requests == []


@pytest.mark.asyncio
async def test_a_plate_that_drifted_in_storage_is_never_inspected() -> None:
    state, storage, _ = await _stage_state()
    await storage.put(
        key=SCENE_KEY,
        data=_png((1080, 1080), (200, 10, 10, 255)),
        content_type="image/png",
    )
    vision = _StubVision(_readout())

    with pytest.raises(ValueError, match="disagree with the recorded scene checksum"):
        await ScenePurityStageHandler(_providers(vision, storage)).execute(_context(state))

    assert vision.requests == []


@pytest.mark.asyncio
async def test_the_inspector_never_sees_a_provider_that_is_not_vision() -> None:
    state, storage, _ = await _stage_state()
    handler = ScenePurityStageHandler(_providers(MockVisionProvider(), storage))

    # The mock echoes the prompt instead of answering it, so purity cannot be
    # certified and the stage refuses rather than guessing.
    with pytest.raises(ProviderResponseError):
        await handler.execute(_context(state))
