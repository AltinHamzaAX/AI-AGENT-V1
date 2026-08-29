from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.asset_intelligence import AssetIntelligenceResult
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.domain.semantic_contract import PostSemanticContract


class HierarchyElement(StrEnum):
    PRODUCT = "product"
    HEADLINE = "headline"
    SUPPORTING_COPY = "supporting_copy"
    OFFER = "offer"
    CTA = "cta"
    LOGO = "logo"


class HierarchyStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=6)
    element: HierarchyElement
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return _text(value)


class ArtDirectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept: CreativeDirection
    copy_draft: CopyDraft
    brand: BrandAnalysis
    assets: AssetIntelligenceResult
    platform: str = Field(min_length=1, max_length=100)
    semantic_contract: dict[str, Any]

    @model_validator(mode="after")
    def inputs_must_describe_one_post(self) -> "ArtDirectorInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("art director requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.concept.contract_fingerprint,
            self.copy_draft.contract_fingerprint,
            self.brand.contract_fingerprint,
            self.assets.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("art director inputs disagree on the semantic contract")
        if _semantic(self.platform) != _semantic(contract.platform):
            raise ValueError("art director platform disagrees with the semantic contract")
        return self


class ArtDirectionLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focal_point: str = Field(min_length=1, max_length=600)
    composition: str = Field(min_length=1, max_length=1_000)
    visual_hierarchy: list[HierarchyStep] = Field(min_length=4, max_length=6)
    product_dominance: float = Field(ge=0, le=1)
    negative_space: str = Field(min_length=1, max_length=600)
    photography_direction: str = Field(min_length=1, max_length=1_000)
    lighting: str = Field(min_length=1, max_length=600)
    typography_direction: str = Field(min_length=1, max_length=800)
    color_direction: str = Field(min_length=1, max_length=800)
    graphic_language: str = Field(min_length=1, max_length=800)
    cta_treatment: str = Field(min_length=1, max_length=600)
    logo_region: str = Field(min_length=1, max_length=600)

    @field_validator(
        "focal_point",
        "composition",
        "negative_space",
        "photography_direction",
        "lighting",
        "typography_direction",
        "color_direction",
        "graphic_language",
        "cta_treatment",
        "logo_region",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def hierarchy_is_unique_and_ranked(self) -> "ArtDirectionLLMOutput":
        ranks = [step.rank for step in self.visual_hierarchy]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("visual hierarchy ranks must be consecutive and ordered")
        elements = [step.element for step in self.visual_hierarchy]
        if len(elements) != len(set(elements)):
            raise ValueError("visual hierarchy elements must be unique")
        return self


class ArtDirectionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    passed: bool
    detail: str


class ArtDirectionQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[ArtDirectionCheck] = Field(min_length=5, max_length=5)

    @property
    def failures(self) -> list[ArtDirectionCheck]:
        return [check for check in self.checks if not check.passed]


class ArtDirection(ArtDirectionLLMOutput):
    model_config = ConfigDict(extra="forbid")

    quality: ArtDirectionQuality
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def quality_must_pass(self) -> "ArtDirection":
        if self.quality.failures:
            raise ValueError("art direction contains failed quality checks")
        return self


def _text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("art direction text cannot be blank")
    return normalized


def _semantic(value: str) -> str:
    return " ".join(value.casefold().split())


__all__ = [
    "ArtDirection",
    "ArtDirectionCheck",
    "ArtDirectionLLMOutput",
    "ArtDirectionQuality",
    "ArtDirectorInput",
    "HierarchyElement",
    "HierarchyStep",
]
