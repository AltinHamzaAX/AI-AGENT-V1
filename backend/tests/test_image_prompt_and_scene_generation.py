import io
import re
from uuid import uuid4

import pytest
from PIL import Image
from test_art_director_agent import _input as _art_input
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload
from test_generation_planner import _asset

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockLLMProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration import ProductionStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import ImageRequest, ImageResponse, ProviderBundle
from app.modules.posts.tools.generation import (
    GenerationDecision,
    GenerationPlanner,
    GenerationPlannerInput,
    ImagePromptBuilder,
    SceneGenerationStatus,
    ScenePolicyRule,
    ScenePromptInput,
)


class _SizedImageProvider:
    def __init__(self, *, invalid_size: bool = False) -> None:
        self.requests: list[ImageRequest] = []
        self.invalid_size = invalid_size

    async def generate(self, request: ImageRequest) -> ImageResponse:
        self.requests.append(request)
        size = (1, 1) if self.invalid_size else (request.width, request.height)
        assert size[0] is not None and size[1] is not None
        image = Image.new("RGB", size, "navy")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ImageResponse(
            image=buffer.getvalue(),
            mime_type="image/png",
            provider="test-image",
            model="scene-test",
        )


async def _fixture() -> tuple[ScenePromptInput, dict]:
    design_input = await _design_input()
    art_input = await _art_input()
    fingerprint = design_input.art_direction.contract_fingerprint
    design_spec = DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint)
    creative_concept = art_input.concept.model_copy(update={"contract_fingerprint": fingerprint})
    plan = GenerationPlanner().build(
        GenerationPlannerInput(
            assets=[],
            design_spec=design_spec,
            semantic_contract=design_input.semantic_contract,
        )
    )
    payload = ScenePromptInput(
        semantic_contract=design_input.semantic_contract,
        creative_concept=creative_concept,
        art_direction=design_input.art_direction,
        design_spec=design_spec,
        asset_policies=[],
        generation_plan=plan,
    )
    state = {
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
        PostWorkflowSection.CREATIVE_CONCEPT.value: payload.creative_concept.model_dump(
            mode="json"
        ),
        PostWorkflowSection.ART_DIRECTION.value: payload.art_direction.model_dump(mode="json"),
        PostWorkflowSection.DESIGN_SPEC.value: payload.design_spec.model_dump(mode="json"),
        PostWorkflowSection.ASSETS.value: [],
        PostWorkflowSection.GENERATION_PLAN.value: plan.model_dump(mode="json"),
    }
    return payload, state


def _providers(image: _SizedImageProvider, storage: MockStorageProvider) -> ProviderBundle:
    return ProviderBundle(
        llm=MockLLMProvider(),
        vision=MockVisionProvider(),
        image=image,
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=storage,
    )


@pytest.mark.asyncio
async def test_prompt_consumes_every_contract_and_is_scene_only() -> None:
    payload, _ = await _fixture()
    prompt = ImagePromptBuilder().build(payload)
    contract = payload.semantic_contract

    assert set(prompt.policy_rules) == set(ScenePolicyRule)
    assert prompt.width == payload.design_spec.canvas.width
    assert prompt.height == payload.design_spec.canvas.height
    assert payload.design_spec.background.rstrip(".") in prompt.positive_prompt
    assert payload.design_spec.lighting.rstrip(".") in prompt.positive_prompt
    assert "Negative space:" in prompt.positive_prompt
    assert (
        "Generate environment, lighting, photography, atmosphere and texture only"
        in prompt.positive_prompt
    )
    for key in ("company", "brand", "product", "primary_entity", "offer", "cta_intent"):
        value = contract[key]
        if value:
            assert value.casefold() not in prompt.positive_prompt.casefold()
    for forbidden in ("vehicle", "car", "logo", "price", "call to action", "watermark"):
        assert not re.search(
            rf"\b{re.escape(forbidden)}\b", prompt.positive_prompt, flags=re.IGNORECASE
        )
    assert "watermark or signature" in prompt.negative_prompt
    assert prompt == ImagePromptBuilder().build(payload)


@pytest.mark.asyncio
async def test_production_generates_validates_and_persists_scene() -> None:
    _, state = await _fixture()
    image = _SizedImageProvider()
    storage = MockStorageProvider()
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )

    result = await ProductionStageHandler(_providers(image, storage)).execute(context)

    artifacts = result.outputs[PostWorkflowSection.GENERATION_ARTIFACTS]
    assert len(artifacts) == 1
    assert artifacts[0]["status"] == SceneGenerationStatus.GENERATED
    assert artifacts[0]["provider"] == "test-image"
    assert artifacts[0]["storage_key"] in storage.objects
    assert len(image.requests) == 1
    assert image.requests[0].negative_prompt


@pytest.mark.asyncio
async def test_protected_asset_policy_reserves_exact_composition_region() -> None:
    payload, _ = await _fixture()
    policy = _asset(
        IntelligentAssetRole.PRIMARY_PRODUCT,
        payload.design_spec.contract_fingerprint,
    )
    plan = GenerationPlanner().build(
        GenerationPlannerInput(
            assets=[policy],
            design_spec=payload.design_spec,
            semantic_contract=payload.semantic_contract,
        )
    )
    protected_payload = payload.model_copy(
        update={"asset_policies": [policy], "generation_plan": plan}
    )

    prompt = ImagePromptBuilder().build(protected_payload)
    bounds = payload.design_spec.regions.product_bounds

    assert plan.decision is GenerationDecision.GENERATE_BACKGROUND
    assert f"x={bounds.x}, y={bounds.y}, width={bounds.width}" in prompt.positive_prompt
    assert str(policy.asset_id) not in prompt.positive_prompt


@pytest.mark.asyncio
async def test_compose_only_skips_provider_and_storage() -> None:
    payload, state = await _fixture()
    compose_only = payload.generation_plan.model_copy(
        update={
            "decision": GenerationDecision.COMPOSE_ONLY,
            "task": None,
            "may_generate": [],
            "estimated_image_calls": 0,
            "cost_tier": "none",
        }
    )
    state[PostWorkflowSection.GENERATION_PLAN.value] = compose_only.model_dump(mode="json")
    image = _SizedImageProvider()
    storage = MockStorageProvider()
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )

    result = await ProductionStageHandler(_providers(image, storage)).execute(context)

    artifact = result.outputs[PostWorkflowSection.GENERATION_ARTIFACTS][0]
    assert artifact["status"] == SceneGenerationStatus.SKIPPED
    assert image.requests == []
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_wrong_provider_dimensions_fail_before_persistence() -> None:
    _, state = await _fixture()
    storage = MockStorageProvider()
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )

    with pytest.raises(ValueError, match="expected 1080x1080"):
        await ProductionStageHandler(
            _providers(_SizedImageProvider(invalid_size=True), storage)
        ).execute(context)

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_retry_uses_the_same_storage_key_without_duplicate_artifacts() -> None:
    _, state = await _fixture()
    image = _SizedImageProvider()
    storage = MockStorageProvider()
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )
    handler = ProductionStageHandler(_providers(image, storage))

    first = await handler.execute(context)
    second = await handler.execute(context)

    first_artifact = first.outputs[PostWorkflowSection.GENERATION_ARTIFACTS][0]
    second_artifact = second.outputs[PostWorkflowSection.GENERATION_ARTIFACTS][0]
    assert first_artifact["storage_key"] == second_artifact["storage_key"]
    assert first_artifact["checksum"] == second_artifact["checksum"]
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_contract_drift_fails_before_provider() -> None:
    payload, _ = await _fixture()
    drifted = payload.model_copy(
        update={
            "design_spec": payload.design_spec.model_copy(update={"contract_fingerprint": "0" * 64})
        }
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        ScenePromptInput.model_validate(drifted.model_dump(mode="json"))


def test_supervisor_declares_complete_production_contract() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.PRODUCTION)
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.CREATIVE_CONCEPT,
        PostWorkflowSection.ART_DIRECTION,
        PostWorkflowSection.DESIGN_SPEC,
        PostWorkflowSection.ASSETS,
        PostWorkflowSection.GENERATION_PLAN,
    }
    assert policy.output_sections == (PostWorkflowSection.GENERATION_ARTIFACTS,)
