from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.shared.assets.domain import AssetRole


class IntelligentAssetRole(StrEnum):
    BRAND_LOGO = "brand_logo"
    PRIMARY_PRODUCT = "primary_product"
    VEHICLE = "vehicle"
    PACKAGING = "packaging"
    ENVIRONMENT = "environment"
    BACKGROUND_REFERENCE = "background_reference"
    STYLE_REFERENCE = "style_reference"
    SUPPORTING_ASSET = "supporting_asset"
    INSPIRATION_ONLY = "inspiration_only"


class AssetAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    declared_role: AssetRole
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dimensions_are_complete(self) -> "AssetAttachmentInput":
        if (self.width is None) != (self.height is None):
            raise ValueError("asset width and height must be provided together")
        return self


class AssetIntelligenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, JsonValue]
    latest_message: str = Field(default="", max_length=20_000)
    conversation_history: list[str] = Field(default_factory=list, max_length=200)
    attachments: list[AssetAttachmentInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def attachment_ids_are_unique(self) -> "AssetIntelligenceInput":
        identifiers = [attachment.id for attachment in self.attachments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("asset IDs must be unique")
        return self


class AssetRoleClassification(BaseModel):
    """Provider proposal. Policy fields are never delegated to the provider."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    role: IntelligentAssetRole
    user_intent_evidence: str | None = Field(default=None, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)


class AssetIntelligenceLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifications: list[AssetRoleClassification] = Field(max_length=50)


class AssetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    original_filename: str
    role: IntelligentAssetRole
    required: bool
    preserve_identity: bool
    allow_crop: bool
    allow_replace: bool
    allow_generation: bool
    min_dominance: float = Field(ge=0, le=1)
    max_dominance: float = Field(ge=0, le=1)
    user_intent_evidence: str | None = None
    classification_reason: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def dominance_range_is_ordered(self) -> "AssetPolicy":
        if self.min_dominance > self.max_dominance:
            raise ValueError("min_dominance cannot exceed max_dominance")
        if self.preserve_identity and (self.allow_replace or self.allow_generation):
            raise ValueError("identity-preserved assets cannot be replaced or generated")
        return self


class AssetIntelligenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: list[AssetPolicy] = Field(max_length=50)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class AssetUsageAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    used: bool
    identity_preserved: bool | None = None
    cropped: bool = False
    replaced_by: UUID | None = None
    generated_substitute: bool = False
    dominance: float | None = Field(default=None, ge=0, le=1)


class AssetPolicyValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    decision: Literal["CONTINUE", "HARD_FAIL"]
    violations: list[str] = Field(default_factory=list)


__all__ = [
    "AssetAttachmentInput",
    "AssetIntelligenceInput",
    "AssetIntelligenceLLMOutput",
    "AssetIntelligenceResult",
    "AssetPolicy",
    "AssetPolicyValidation",
    "AssetRoleClassification",
    "AssetUsageAssertion",
    "IntelligentAssetRole",
]
