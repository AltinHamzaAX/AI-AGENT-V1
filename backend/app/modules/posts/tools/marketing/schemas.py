from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.agents.brand_product import ProductAnalysis
from app.modules.posts.domain.semantic_contract import PostSemanticContract


class MarketingFrameworkInput(BaseModel):
    """Verified upstream facts available to deterministic marketing tools."""

    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, Any]
    product: ProductAnalysis
    audience: AudienceIntelligence

    @model_validator(mode="after")
    def inputs_must_describe_one_post(self) -> "MarketingFrameworkInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("marketing framework tools require a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.product.contract_fingerprint,
            self.audience.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("marketing framework inputs disagree on the semantic contract")
        return self


class EvidenceOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=750)
    basis: list[str] = Field(min_length=1, max_length=20)


class STPResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: EvidenceOption
    segments: list[EvidenceOption] = Field(min_length=1, max_length=10)
    target: EvidenceOption


class FeatureBenefitValueOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str = Field(min_length=1, max_length=500)
    benefit: str = Field(min_length=1, max_length=500)
    customer_value: str = Field(min_length=1, max_length=500)
    basis: list[str] = Field(min_length=1, max_length=20)


class FeatureBenefitMapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[FeatureBenefitValueOption] = Field(default_factory=list, max_length=100)


class USPExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[EvidenceOption] = Field(default_factory=list, max_length=50)


class PositioningFrameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: EvidenceOption
    customer_tension: EvidenceOption
    differentiators: list[EvidenceOption] = Field(min_length=1, max_length=150)


class ValuePropositionFrameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: EvidenceOption
    customer_needs: list[EvidenceOption] = Field(min_length=1, max_length=60)
    customer_values: list[EvidenceOption] = Field(min_length=1, max_length=100)


class FrameworkKind(StrEnum):
    AIDA = "aida"
    PAS = "pas"
    NONE = "none"


class MessageStrategyFrameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: EvidenceOption
    customer_tension: EvidenceOption
    value_options: list[EvidenceOption] = Field(min_length=1, max_length=100)
    eligible_frameworks: list[FrameworkKind] = Field(min_length=1, max_length=3)
    single_message_required: bool = True


class CTAFrameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: EvidenceOption
    objective: EvidenceOption
    constraints: list[str] = Field(default_factory=list, max_length=100)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=100)
    must_be_direct: bool = True


class MarketingFrameworkContext(BaseModel):
    """All tool-produced scaffolding supplied to the decision-making agent."""

    model_config = ConfigDict(extra="forbid")

    stp: STPResult
    feature_benefit: FeatureBenefitMapResult
    usp: USPExtractionResult
    positioning: PositioningFrameResult
    value_proposition: ValuePropositionFrameResult
    message_strategy: MessageStrategyFrameResult
    cta: CTAFrameResult


__all__ = [
    "CTAFrameResult",
    "EvidenceOption",
    "FeatureBenefitMapResult",
    "FeatureBenefitValueOption",
    "FrameworkKind",
    "MarketingFrameworkContext",
    "MarketingFrameworkInput",
    "MessageStrategyFrameResult",
    "PositioningFrameResult",
    "STPResult",
    "USPExtractionResult",
    "ValuePropositionFrameResult",
]
