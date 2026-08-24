from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis


class InsightConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PurchaseIntentLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class AudienceIntelligenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, Any]
    brand: BrandAnalysis
    product: ProductAnalysis


class AudienceInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight: str = Field(min_length=1, max_length=750)
    basis: list[str] = Field(min_length=1, max_length=20)
    confidence: InsightConfidence

    @field_validator("insight")
    @classmethod
    def normalize_insight(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class AudienceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=750)
    parent_audience: str | None = Field(default=None, max_length=500)
    basis: list[str] = Field(min_length=1, max_length=20)
    confidence: InsightConfidence

    @field_validator("name", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class AudienceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=750)
    basis: list[str] = Field(min_length=1, max_length=20)
    confidence: InsightConfidence

    @field_validator("segment", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class PurchaseIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: PurchaseIntentLevel
    rationale: str = Field(min_length=1, max_length=750)
    basis: list[str] = Field(min_length=1, max_length=20)
    confidence: InsightConfidence

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class CustomerTension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_state: str = Field(min_length=1, max_length=750)
    desired_state: str = Field(min_length=1, max_length=750)
    tension: str = Field(min_length=1, max_length=750)
    basis: list[str] = Field(min_length=1, max_length=20)
    confidence: InsightConfidence

    @field_validator("current_state", "desired_state", "tension")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class AudienceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declared_audience: str
    market: str | None
    location: str | None
    platform: str
    situations: list[AudienceInsight] = Field(min_length=1, max_length=30)


class AudienceIntelligenceLLMOutput(BaseModel):
    """Audience hypotheses only; research and strategy fields are deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    segments: list[AudienceSegment] = Field(min_length=1, max_length=10)
    target: AudienceTarget
    needs: list[AudienceInsight] = Field(min_length=1, max_length=30)
    desires: list[AudienceInsight] = Field(min_length=1, max_length=30)
    pain_points: list[AudienceInsight] = Field(min_length=1, max_length=30)
    objections: list[AudienceInsight] = Field(min_length=1, max_length=30)
    motivation: list[AudienceInsight] = Field(min_length=1, max_length=30)
    purchase_intent: PurchaseIntent
    trust_triggers: list[AudienceInsight] = Field(min_length=1, max_length=30)
    situations: list[AudienceInsight] = Field(min_length=1, max_length=30)
    customer_tension: CustomerTension

    @model_validator(mode="after")
    def target_must_reference_a_segment(self) -> "AudienceIntelligenceLLMOutput":
        segment_names = {segment.name.casefold() for segment in self.segments}
        if self.target.segment.casefold() not in segment_names:
            raise ValueError("target must reference a declared audience segment")
        return self


class AudienceIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[AudienceSegment] = Field(min_length=1, max_length=10)
    target: AudienceTarget
    needs: list[AudienceInsight] = Field(min_length=1, max_length=30)
    desires: list[AudienceInsight] = Field(min_length=1, max_length=30)
    pain_points: list[AudienceInsight] = Field(min_length=1, max_length=30)
    objections: list[AudienceInsight] = Field(min_length=1, max_length=30)
    motivation: list[AudienceInsight] = Field(min_length=1, max_length=30)
    purchase_intent: PurchaseIntent
    trust_triggers: list[AudienceInsight] = Field(min_length=1, max_length=30)
    context: AudienceContext
    customer_tension: CustomerTension
    limitations: list[str] = Field(min_length=1, max_length=20)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


def _normalized_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text cannot be blank")
    return normalized


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _normalized_text(value)
        if normalized not in result:
            result.append(normalized)
    return result


__all__ = [
    "AudienceContext",
    "AudienceInsight",
    "AudienceIntelligence",
    "AudienceIntelligenceInput",
    "AudienceIntelligenceLLMOutput",
    "AudienceSegment",
    "AudienceTarget",
    "CustomerTension",
    "InsightConfidence",
    "PurchaseIntent",
    "PurchaseIntentLevel",
]
