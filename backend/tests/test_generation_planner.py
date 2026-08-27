from uuid import uuid4

import pytest
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload

from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration.generation_planning import (
    GenerationPlanningStageHandler,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.tools.generation import (
    AssetCategory,
    GenerationDecision,
    GenerationKind,
    GenerationPlanner,
    GenerationPlannerInput,
)


async def _input(roles: list[IntelligentAssetRole]) -> GenerationPlannerInput:
    design_input = await _design_input()
    fingerprint = design_input.copy_draft.contract_fingerprint
    return GenerationPlannerInput(
        assets=[_asset(role, fingerprint) for role in roles],
        design_spec=DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint),
        semantic_contract=design_input.semantic_contract,
    )


def _asset(role: IntelligentAssetRole, fingerprint: str) -> AssetPolicy:
    protected = role in {
        IntelligentAssetRole.BRAND_LOGO,
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
    return AssetPolicy(
        asset_id=uuid4(),
        original_filename=f"{role.value}.png",
        role=role,
        required=protected,
        preserve_identity=protected,
        allow_crop=not protected,
        allow_replace=False,
        allow_generation=False,
        min_dominance=0.1,
        max_dominance=0.8,
        classification_reason="test fixture",
        contract_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_logo_product_background_selects_compose_only() -> None:
    payload = await _input(
        [
            IntelligentAssetRole.BRAND_LOGO,
            IntelligentAssetRole.PRIMARY_PRODUCT,
            IntelligentAssetRole.ENVIRONMENT,
        ]
    )
    result = GenerationPlanner().build(payload)

    assert result.decision is GenerationDecision.COMPOSE_ONLY
    assert result.inventory.has_logo
    assert result.inventory.has_product
    assert result.inventory.has_background
    assert result.task is None
    assert result.may_generate == []
    assert result.estimated_image_calls == 0
    assert result.cost_tier == "none"


@pytest.mark.asyncio
async def test_logo_product_without_background_generates_background_only() -> None:
    payload = await _input(
        [IntelligentAssetRole.BRAND_LOGO, IntelligentAssetRole.PRIMARY_PRODUCT]
    )
    result = GenerationPlanner().build(payload)

    assert result.decision is GenerationDecision.GENERATE_BACKGROUND
    assert result.missing == [AssetCategory.BACKGROUND]
    assert result.may_generate == [GenerationKind.BACKGROUND]
    assert result.task is not None
    assert result.task.kind is GenerationKind.BACKGROUND
    assert result.estimated_image_calls == 1
    assert "generated replacement product" in result.task.prohibited_content


@pytest.mark.asyncio
async def test_no_useful_visual_generates_unbranded_scene() -> None:
    payload = await _input([IntelligentAssetRole.BRAND_LOGO])
    result = GenerationPlanner().build(payload)

    assert result.decision is GenerationDecision.GENERATE_SCENE
    assert not result.inventory.has_useful_visual
    assert result.task is not None
    assert result.task.kind is GenerationKind.SCENE
    assert "unbranded environment and atmosphere only" in result.task.allowed_content
    assert "logo or brand mark" in result.task.prohibited_content
    assert all("vehicle" not in item.casefold() for item in result.task.allowed_content)


@pytest.mark.asyncio
async def test_protected_assets_are_always_preserved_not_generated() -> None:
    payload = await _input(
        [IntelligentAssetRole.BRAND_LOGO, IntelligentAssetRole.VEHICLE]
    )
    result = GenerationPlanner().build(payload)

    assert {item.asset_id for item in result.preserve} == {
        asset.asset_id for asset in payload.assets
    }
    assert all(item.preserve_identity for item in result.preserve)
    assert result.task is not None
    assert set(result.task.preserve_asset_ids) == {
        asset.asset_id for asset in payload.assets
    }


@pytest.mark.asyncio
async def test_existing_background_visual_avoids_generation_for_service_post() -> None:
    result = GenerationPlanner().build(
        await _input(
            [IntelligentAssetRole.BRAND_LOGO, IntelligentAssetRole.ENVIRONMENT]
        )
    )
    assert result.decision is GenerationDecision.COMPOSE_ONLY
    assert not result.inventory.has_product
    assert result.estimated_image_calls == 0


@pytest.mark.asyncio
async def test_contract_drift_is_rejected_before_planning() -> None:
    payload = await _input([IntelligentAssetRole.PRIMARY_PRODUCT])
    drifted = payload.model_copy(
        update={
            "assets": [
                payload.assets[0].model_copy(update={"contract_fingerprint": "0" * 64})
            ]
        }
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        GenerationPlannerInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_stage_writes_only_generation_plan() -> None:
    payload = await _input(
        [IntelligentAssetRole.BRAND_LOGO, IntelligentAssetRole.PRIMARY_PRODUCT]
    )
    state = {
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
        PostWorkflowSection.DESIGN_SPEC.value: payload.design_spec.model_dump(mode="json"),
        PostWorkflowSection.ASSETS.value: [
            asset.model_dump(mode="json") for asset in payload.assets
        ],
    }
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )
    result = await GenerationPlanningStageHandler().execute(context)
    assert set(result.outputs) == {PostWorkflowSection.GENERATION_PLAN}
    assert (
        result.outputs[PostWorkflowSection.GENERATION_PLAN]["decision"]
        == "GENERATE_BACKGROUND"
    )


def test_supervisor_requires_assets_design_and_contract() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.GENERATION_PLANNING)
    assert set(policy.required_sections) == {
        PostWorkflowSection.DESIGN_SPEC,
        PostWorkflowSection.REFERENCE_VALIDATION,
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.ASSETS,
    }
    assert policy.output_sections == (PostWorkflowSection.GENERATION_PLAN,)


@pytest.mark.asyncio
async def test_planner_is_deterministic_and_calls_no_provider() -> None:
    payload = await _input([IntelligentAssetRole.PRIMARY_PRODUCT])
    before = payload.model_dump(mode="json")
    first = GenerationPlanner().build(payload)
    second = GenerationPlanner().build(payload)
    assert first == second
    assert payload.model_dump(mode="json") == before
