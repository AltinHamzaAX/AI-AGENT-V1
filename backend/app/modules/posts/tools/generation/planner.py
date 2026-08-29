from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.asset_intelligence import (
    AssetPolicy,
    IntelligentAssetRole,
)
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.semantic_contract import PostSemanticContract

GENERATION_PLAN_SCHEMA_VERSION = "1.0"


class GenerationDecision(StrEnum):
    COMPOSE_ONLY = "COMPOSE_ONLY"
    GENERATE_BACKGROUND = "GENERATE_BACKGROUND"
    GENERATE_SCENE = "GENERATE_SCENE"


class AssetCategory(StrEnum):
    LOGO = "logo"
    PRODUCT = "product"
    BACKGROUND = "background"
    USEFUL_VISUAL = "useful_visual"


class GenerationKind(StrEnum):
    BACKGROUND = "background"
    SCENE = "scene"


class AssetInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    has_logo: bool
    has_product: bool
    has_background: bool
    has_useful_visual: bool
    asset_ids: list[UUID]
    roles: list[IntelligentAssetRole]


class PreserveDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    role: IntelligentAssetRole
    preserve_identity: bool
    allow_crop: bool
    reason: str


class GenerationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: GenerationKind
    allowed_content: list[str] = Field(min_length=1)
    prohibited_content: list[str] = Field(min_length=6)
    preserve_asset_ids: list[UUID]
    output_count: int = Field(default=1, ge=1, le=4)


class GenerationPlannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assets: list[AssetPolicy] = Field(max_length=50)
    design_spec: DesignSpec
    semantic_contract: dict

    @model_validator(mode="after")
    def inputs_share_contract(self) -> "GenerationPlannerInput":
        contract = PostSemanticContract.from_dict(self.semantic_contract)
        fingerprints = {
            contract.fingerprint,
            self.design_spec.contract_fingerprint,
            *(asset.contract_fingerprint for asset in self.assets),
        }
        if len(fingerprints) != 1:
            raise ValueError("generation planner inputs disagree on the semantic contract")
        identifiers = [asset.asset_id for asset in self.assets]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("generation planner asset IDs must be unique")
        return self


class GenerationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = GENERATION_PLAN_SCHEMA_VERSION
    decision: GenerationDecision
    inventory: AssetInventory
    available: list[AssetCategory]
    missing: list[AssetCategory]
    preserve: list[PreserveDirective]
    may_generate: list[GenerationKind]
    task: GenerationTask | None
    estimated_image_calls: int = Field(ge=0, le=4)
    cost_tier: str
    reason: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def decision_matches_work(self) -> "GenerationPlan":
        if self.decision is GenerationDecision.COMPOSE_ONLY:
            if self.task is not None or self.estimated_image_calls != 0 or self.may_generate:
                raise ValueError("COMPOSE_ONLY cannot schedule image generation")
        elif self.task is None or self.estimated_image_calls < 1:
            raise ValueError("generation decisions require exactly scoped work")
        return self


class GenerationPlanner:
    _PRODUCT_ROLES = {
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
    _BACKGROUND_ROLES = {
        IntelligentAssetRole.ENVIRONMENT,
        IntelligentAssetRole.BACKGROUND_REFERENCE,
    }
    _USEFUL_ROLES = _PRODUCT_ROLES | _BACKGROUND_ROLES | {
        IntelligentAssetRole.SUPPORTING_ASSET,
    }
    _PROHIBITED = [
        "promotional text",
        "headline",
        "offer or price",
        "CTA",
        "logo or brand mark",
        "watermark",
        "generated replacement product",
    ]

    def build(self, payload: GenerationPlannerInput) -> GenerationPlan:
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        roles = {asset.role for asset in payload.assets}
        inventory = AssetInventory(
            has_logo=IntelligentAssetRole.BRAND_LOGO in roles,
            has_product=bool(roles & self._PRODUCT_ROLES),
            has_background=bool(roles & self._BACKGROUND_ROLES),
            has_useful_visual=bool(roles & self._USEFUL_ROLES),
            asset_ids=[asset.asset_id for asset in payload.assets],
            roles=sorted(roles, key=lambda role: role.value),
        )
        available, missing = _availability(inventory)
        preserve = [
            PreserveDirective(
                asset_id=asset.asset_id,
                role=asset.role,
                preserve_identity=asset.preserve_identity,
                allow_crop=asset.allow_crop,
                reason=(
                    "Protected source asset; compose the original bytes."
                    if asset.preserve_identity
                    else "Reuse the approved source asset according to its crop policy."
                ),
            )
            for asset in payload.assets
            if asset.required or asset.preserve_identity
        ]
        preserve_ids = [item.asset_id for item in preserve]

        if inventory.has_useful_visual and inventory.has_background:
            decision = GenerationDecision.COMPOSE_ONLY
            task = None
            may_generate: list[GenerationKind] = []
            reason = "Useful visual and background assets already exist; skip image generation."
        elif inventory.has_useful_visual:
            decision = GenerationDecision.GENERATE_BACKGROUND
            may_generate = [GenerationKind.BACKGROUND]
            task = GenerationTask(
                kind=GenerationKind.BACKGROUND,
                allowed_content=[
                    payload.design_spec.background,
                    payload.design_spec.lighting,
                    "environment only; leave protected product and logo to composition",
                ],
                prohibited_content=self._PROHIBITED,
                preserve_asset_ids=preserve_ids,
            )
            reason = "A useful focal visual exists, but no usable background is available."
        else:
            decision = GenerationDecision.GENERATE_SCENE
            may_generate = [GenerationKind.SCENE]
            task = GenerationTask(
                kind=GenerationKind.SCENE,
                allowed_content=[
                    payload.design_spec.lighting,
                    payload.design_spec.background,
                    "unbranded environment and atmosphere only",
                    "reserve composition space for any later approved focal asset",
                ],
                prohibited_content=self._PROHIBITED,
                preserve_asset_ids=preserve_ids,
            )
            reason = "No useful visual exists; generate an unbranded scene for composition."
        calls = 0 if task is None else task.output_count
        return GenerationPlan(
            decision=decision,
            inventory=inventory,
            available=available,
            missing=missing,
            preserve=preserve,
            may_generate=may_generate,
            task=task,
            estimated_image_calls=calls,
            cost_tier="none" if calls == 0 else "single_generation",
            reason=reason,
            contract_fingerprint=contract.fingerprint,
        )


def _availability(
    inventory: AssetInventory,
) -> tuple[list[AssetCategory], list[AssetCategory]]:
    states = {
        AssetCategory.LOGO: inventory.has_logo,
        AssetCategory.PRODUCT: inventory.has_product,
        AssetCategory.BACKGROUND: inventory.has_background,
        AssetCategory.USEFUL_VISUAL: inventory.has_useful_visual,
    }
    return (
        [category for category, present in states.items() if present],
        [category for category, present in states.items() if not present],
    )


__all__ = [
    "GENERATION_PLAN_SCHEMA_VERSION",
    "AssetCategory",
    "AssetInventory",
    "GenerationDecision",
    "GenerationKind",
    "GenerationPlan",
    "GenerationPlanner",
    "GenerationPlannerInput",
    "GenerationTask",
    "PreserveDirective",
]
