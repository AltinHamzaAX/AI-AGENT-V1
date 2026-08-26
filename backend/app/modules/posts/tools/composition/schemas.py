import hashlib
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.asset_intelligence import AssetPolicy, IntelligentAssetRole
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import Bounds, DesignSpec
from app.modules.posts.tools.design import LayoutPlan, TypographyPlan

COMPOSITION_SCHEMA_VERSION = "1.0"


class CompositionFailure(StrEnum):
    INVALID_IMAGE = "invalid_image"
    MIME_MISMATCH = "mime_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    MISSING_REQUIRED_ASSET = "missing_required_asset"
    IDENTITY_POLICY_VIOLATION = "identity_policy_violation"
    CONTAMINATED_SCENE = "contaminated_scene"
    FONT_UNAVAILABLE = "font_unavailable"
    TEXT_OVERFLOW = "text_overflow"
    EXPORT_TOO_LARGE = "export_too_large"


class CompositionError(ValueError):
    def __init__(self, failure: CompositionFailure, detail: str) -> None:
        self.failure = failure
        self.detail = detail
        super().__init__(f"{failure.value}: {detail}")


class SourceVisual(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    asset_id: UUID
    role: IntelligentAssetRole
    image_bytes: bytes = Field(min_length=1, exclude=True)
    mime_type: str = Field(pattern=r"^image/")
    source_checksum: str | None = Field(default=None, min_length=64, max_length=64)

    def verified_checksum(self) -> str:
        checksum = hashlib.sha256(self.image_bytes).hexdigest()
        if self.source_checksum is not None and self.source_checksum != checksum:
            raise CompositionError(
                CompositionFailure.CHECKSUM_MISMATCH,
                f"source checksum disagrees for asset {self.asset_id}",
            )
        return checksum


class ComposerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scene: SourceVisual | None = None
    products: list[SourceVisual] = Field(default_factory=list, max_length=5)
    logo: SourceVisual | None = None
    copy_draft: CopyDraft
    design_spec: DesignSpec
    asset_policies: list[AssetPolicy] = Field(default_factory=list, max_length=50)
    legal_text: str | None = Field(default=None, max_length=2_000)
    final_scale: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def inputs_are_one_safe_composition(self) -> "ComposerInput":
        fingerprints = {
            self.copy_draft.contract_fingerprint,
            self.design_spec.contract_fingerprint,
            *(policy.contract_fingerprint for policy in self.asset_policies),
        }
        if len(fingerprints) != 1:
            raise ValueError("composer inputs disagree on the semantic contract")
        visuals = [*self.products, *([self.logo] if self.logo else [])]
        visual_ids = [visual.asset_id for visual in visuals]
        if len(visual_ids) != len(set(visual_ids)):
            raise ValueError("composer source asset IDs must be unique")
        policies = {policy.asset_id: policy for policy in self.asset_policies}
        if len(policies) != len(self.asset_policies):
            raise ValueError("composer asset policy IDs must be unique")
        if self.logo is not None and self.logo.role is not IntelligentAssetRole.BRAND_LOGO:
            raise ValueError("logo source must have the brand_logo role")
        product_roles = {
            IntelligentAssetRole.PRIMARY_PRODUCT,
            IntelligentAssetRole.VEHICLE,
            IntelligentAssetRole.PACKAGING,
        }
        if any(product.role not in product_roles for product in self.products):
            raise ValueError("product sources must use a protected product role")
        return self

    def enforce_asset_policy(self) -> None:
        visuals = [*self.products, *([self.logo] if self.logo else [])]
        visual_ids = {visual.asset_id for visual in visuals}
        policies = {policy.asset_id: policy for policy in self.asset_policies}
        for policy in self.asset_policies:
            if policy.required and policy.asset_id not in visual_ids:
                raise CompositionError(
                    CompositionFailure.MISSING_REQUIRED_ASSET,
                    f"required {policy.role.value} asset {policy.asset_id} is missing",
                )
        for visual in visuals:
            policy = policies.get(visual.asset_id)
            if policy is None:
                raise CompositionError(
                    CompositionFailure.IDENTITY_POLICY_VIOLATION,
                    f"source asset {visual.asset_id} has no approved policy",
                )
            if visual.role is not policy.role:
                raise CompositionError(
                    CompositionFailure.IDENTITY_POLICY_VIOLATION,
                    f"source role disagrees with policy for asset {visual.asset_id}",
                )


class ComponentKind(StrEnum):
    SCENE = "scene"
    GRAPHIC_ELEMENT = "graphic_element"
    PRODUCT = "product"
    TYPOGRAPHY = "typography"
    OFFER = "offer"
    CTA = "cta"
    LOGO = "logo"


class ComponentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    kind: ComponentKind
    bounds: Bounds
    z_index: int = Field(ge=0, le=100)
    source_asset_id: UUID | None = None
    source_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    rendered_checksum: str = Field(min_length=64, max_length=64)
    identity_preserved: bool | None = None
    text: str | None = None
    detail: str


class RenderedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    image_bytes: bytes = Field(min_length=1, exclude=True)
    mime_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    checksum: str = Field(min_length=64, max_length=64)


class CompositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    schema_version: str = COMPOSITION_SCHEMA_VERSION
    working_render: RenderedAsset
    preview: RenderedAsset
    final_asset: RenderedAsset
    components: list[ComponentMetadata] = Field(min_length=1, max_length=100)
    layout_plan: LayoutPlan
    typography_plan: TypographyPlan
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)


class StoredRender(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_key: str = Field(min_length=1, max_length=1_024)
    mime_type: str = "image/png"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    checksum: str = Field(min_length=64, max_length=64)


class PostDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = COMPOSITION_SCHEMA_VERSION
    working_render: StoredRender
    preview: StoredRender
    final_asset: StoredRender
    components: list[ComponentMetadata] = Field(min_length=1, max_length=100)
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)


__all__ = [
    "COMPOSITION_SCHEMA_VERSION",
    "ComponentKind",
    "ComponentMetadata",
    "ComposerInput",
    "CompositionError",
    "CompositionFailure",
    "CompositionResult",
    "RenderedAsset",
    "PostDraft",
    "SourceVisual",
    "StoredRender",
]
