import hashlib
import json
import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.semantic_contract import PostSemanticContract

from .planner import GenerationKind, GenerationPlan

SCENE_PROMPT_SCHEMA_VERSION = "1.0"


class ScenePolicyRule(StrEnum):
    READABLE_PROMOTIONAL_TEXT = "readable promotional text"
    FAKE_LOGO = "fake logo"
    FAKE_BRAND = "fake brand or trademark"
    FAKE_PRICE = "fake price or offer"
    CTA = "call to action"
    UI = "user interface or app screen"
    WATERMARK = "watermark or signature"


class ScenePromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict
    creative_concept: CreativeDirection
    art_direction: ArtDirection
    design_spec: DesignSpec
    asset_policies: list[AssetPolicy] = Field(max_length=50)
    generation_plan: GenerationPlan

    @model_validator(mode="after")
    def inputs_describe_one_post(self) -> "ScenePromptInput":
        contract = PostSemanticContract.from_dict(self.semantic_contract)
        fingerprints = {
            contract.fingerprint,
            self.creative_concept.contract_fingerprint,
            self.art_direction.contract_fingerprint,
            self.design_spec.contract_fingerprint,
            self.generation_plan.contract_fingerprint,
            *(policy.contract_fingerprint for policy in self.asset_policies),
        }
        if len(fingerprints) != 1:
            raise ValueError("scene prompt inputs disagree on the semantic contract")
        policy_ids = {policy.asset_id for policy in self.asset_policies}
        if len(policy_ids) != len(self.asset_policies):
            raise ValueError("scene prompt asset policy IDs must be unique")
        preserve_ids = (
            set(self.generation_plan.task.preserve_asset_ids)
            if self.generation_plan.task is not None
            else set()
        )
        if not preserve_ids.issubset(policy_ids):
            raise ValueError("generation plan references an unknown protected asset")
        return self


class ScenePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCENE_PROMPT_SCHEMA_VERSION
    kind: GenerationKind
    positive_prompt: str = Field(min_length=80, max_length=8_000)
    negative_prompt: str = Field(min_length=40, max_length=2_000)
    width: int = Field(ge=320, le=8192)
    height: int = Field(ge=320, le=8192)
    preserve_asset_ids: list[UUID]
    policy_rules: list[ScenePolicyRule] = Field(
        min_length=len(ScenePolicyRule), max_length=len(ScenePolicyRule)
    )
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    prompt_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def every_policy_rule_is_present(self) -> "ScenePrompt":
        if set(self.policy_rules) != set(ScenePolicyRule):
            raise ValueError("scene prompt must enforce every scene policy rule")
        return self


class ImagePromptBuilder:
    """Compile approved strategy into scene-only provider instructions."""

    def build(self, payload: ScenePromptInput) -> ScenePrompt:
        contract = PostSemanticContract.from_dict(payload.semantic_contract)
        plan_task = payload.generation_plan.task
        if plan_task is None:  # defended by ScenePromptInput; keeps typing explicit
            raise ValueError("generation plan has no task")
        selected = next(
            candidate
            for candidate in payload.creative_concept.big_idea_candidates
            if candidate.id == payload.creative_concept.winning_concept.candidate_id
        )
        hook = next(
            item
            for item in payload.creative_concept.visual_hooks
            if item.id == selected.visual_hook_id
        )
        forbidden_terms = _identity_terms(contract)
        parts = [
            f"Create only a {plan_task.kind.value} plate for later deterministic composition.",
            f"Wordless creative intention: {_sanitize(hook.wordless_read, forbidden_terms)}.",
            f"Visual mechanism: {_sanitize(hook.mechanism, forbidden_terms)}.",
            f"Environment: {_sanitize(payload.design_spec.background, forbidden_terms)}.",
            f"Photography: {_sanitize(payload.design_spec.photography, forbidden_terms)}.",
            f"Lighting: {_sanitize(payload.design_spec.lighting, forbidden_terms)}.",
            f"Composition: {_sanitize(payload.art_direction.composition, forbidden_terms)}.",
            f"Negative space: {_sanitize(payload.art_direction.negative_space, forbidden_terms)}.",
            _asset_reservations(payload, set(plan_task.preserve_asset_ids)),
            "Leave clean reserved space for later deterministic typography.",
            "Generate environment, lighting, photography, atmosphere and texture only.",
        ]
        positive = " ".join(_clean(part) for part in parts)
        _assert_scene_only(positive, forbidden_terms)
        negative = ", ".join(rule.value for rule in ScenePolicyRule)
        fingerprint_payload = {
            "kind": plan_task.kind.value,
            "positive": positive,
            "negative": negative,
            "width": payload.design_spec.canvas.width,
            "height": payload.design_spec.canvas.height,
            "preserve": sorted(str(item) for item in plan_task.preserve_asset_ids),
            "contract": contract.fingerprint,
        }
        prompt_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True).encode()
        ).hexdigest()
        return ScenePrompt(
            kind=plan_task.kind,
            positive_prompt=positive,
            negative_prompt=negative,
            width=payload.design_spec.canvas.width,
            height=payload.design_spec.canvas.height,
            preserve_asset_ids=plan_task.preserve_asset_ids,
            policy_rules=list(ScenePolicyRule),
            contract_fingerprint=contract.fingerprint,
            prompt_fingerprint=prompt_fingerprint,
        )


def _identity_terms(contract: PostSemanticContract) -> tuple[str, ...]:
    values = (
        contract.company,
        contract.brand,
        contract.product,
        contract.primary_entity,
        contract.offer,
        contract.cta_intent,
    )
    return tuple(value.strip() for value in values if isinstance(value, str) and value.strip())


def _sanitize(value: str, forbidden_terms: tuple[str, ...]) -> str:
    result = value
    for term in sorted(forbidden_terms, key=len, reverse=True):
        result = re.sub(re.escape(term), "reserved composition area", result, flags=re.IGNORECASE)
    result = re.sub(
        r"\b(vehicle|car|automobile|product|packaging|package|logo|brand mark|"
        r"headline|offer|price|cta|call to action|copy|promotional text|ui|watermark)\b",
        "reserved composition area",
        result,
        flags=re.IGNORECASE,
    )
    return _clean(result)


def _clean(value: str) -> str:
    normalized = " ".join(value.split()).strip(" ,.;")
    return re.sub(
        r"reserved composition area(?:\s+area|\s+reserved composition area)+",
        "reserved composition area",
        normalized,
        flags=re.IGNORECASE,
    )


def _asset_reservations(payload: ScenePromptInput, preserve_asset_ids: set[UUID]) -> str:
    protected = {
        policy.asset_id: policy
        for policy in payload.asset_policies
        if policy.asset_id in preserve_asset_ids
    }
    instructions: list[str] = []
    product_roles = {
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
    if any(policy.role in product_roles for policy in protected.values()):
        bounds = payload.design_spec.regions.product_bounds
        instructions.append(
            f"keep rectangle x={bounds.x}, y={bounds.y}, width={bounds.width}, "
            f"height={bounds.height} visually quiet for source compositing"
        )
    if any(policy.role is IntelligentAssetRole.BRAND_LOGO for policy in protected.values()):
        bounds = payload.design_spec.regions.logo_region
        instructions.append(
            f"keep rectangle x={bounds.x}, y={bounds.y}, width={bounds.width}, "
            f"height={bounds.height} clear and undecorated"
        )
    if not instructions:
        instructions.append("keep approved composition regions visually clear")
    return "Protected-asset reservations: " + "; ".join(instructions) + "."


def _assert_scene_only(prompt: str, forbidden_terms: tuple[str, ...]) -> None:
    normalized = prompt.casefold()
    leaked = [term for term in forbidden_terms if term.casefold() in normalized]
    if leaked:
        raise ValueError("scene prompt leaked protected brand, product, offer, or CTA content")


__all__ = [
    "SCENE_PROMPT_SCHEMA_VERSION",
    "ImagePromptBuilder",
    "ScenePolicyRule",
    "ScenePrompt",
    "ScenePromptInput",
]
