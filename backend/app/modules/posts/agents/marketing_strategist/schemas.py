from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis
from app.modules.posts.agents.client_understanding import ClientUnderstandingBrief
from app.modules.posts.tools.research import ExternalResearchResult


class MarketingPrinciple(StrEnum):
    """The discipline a decision is answerable to.

    Named on every decision rather than described in a prompt, so that a
    strategy can be checked against the principle it claims to be applying
    instead of being taken at its word.
    """

    STP = "stp"
    POSITIONING = "positioning"
    CUSTOMER_INSIGHT = "customer_insight"
    FEATURE_BENEFIT = "feature_benefit"
    USP = "usp"
    VALUE_PROPOSITION = "value_proposition"
    MESSAGE_STRATEGY = "message_strategy"
    CTA_STRATEGY = "cta_strategy"


class MessageFramework(StrEnum):
    AIDA = "aida"
    PAS = "pas"
    #: A framework is a tool, not a requirement. Forcing PAS onto a brief with
    #: no agitatable problem produces worse strategy than naming no framework.
    NONE = "none"


class MarketingStrategyInput(BaseModel):
    """Everything decided upstream, and nothing decided here."""

    model_config = ConfigDict(extra="forbid")

    brief: ClientUnderstandingBrief
    semantic_contract: dict[str, Any]
    brand: BrandAnalysis
    product: ProductAnalysis
    audience: AudienceIntelligence
    research: ExternalResearchResult


class StrategicDecision(BaseModel):
    """One decision, why it was made, and what it rests on.

    The rationale is required by the schema rather than requested in prose.
    A strategy whose reasoning is optional degrades into assertions, and an
    assertion cannot be reviewed, argued with, or corrected by a later stage.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=750)
    rationale: str = Field(min_length=1, max_length=1_000)
    principle: MarketingPrinciple
    basis: list[str] = Field(min_length=1, max_length=20)

    @field_validator("decision", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


class MessageFrameworkChoice(BaseModel):
    """Which classical structure fits this brief, including none of them."""

    model_config = ConfigDict(extra="forbid")

    framework: MessageFramework
    rationale: str = Field(min_length=1, max_length=1_000)
    basis: list[str] = Field(min_length=1, max_length=20)

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique_strings(values)


#: The decision fields, in the order a strategy is actually reasoned through:
#: objective first, then who, then what we stand for, then what we say.
STRATEGY_DECISIONS = (
    "business_objective",
    "segmentation",
    "targeting",
    "positioning",
    "customer_insight",
    "customer_tension",
    "usp",
    "value_proposition",
    "marketing_angle",
    "single_minded_message",
    "desired_reaction",
    "cta_strategy",
)

#: Which principle each decision answers to. Fixed by us, never chosen by the
#: model: a strategy that labels its targeting call "positioning" has not made
#: a positioning decision, it has mislabelled a targeting one.
DECISION_PRINCIPLES: dict[str, MarketingPrinciple] = {
    "business_objective": MarketingPrinciple.STP,
    "segmentation": MarketingPrinciple.STP,
    "targeting": MarketingPrinciple.STP,
    "positioning": MarketingPrinciple.POSITIONING,
    "customer_insight": MarketingPrinciple.CUSTOMER_INSIGHT,
    "customer_tension": MarketingPrinciple.CUSTOMER_INSIGHT,
    "usp": MarketingPrinciple.USP,
    "value_proposition": MarketingPrinciple.VALUE_PROPOSITION,
    "marketing_angle": MarketingPrinciple.MESSAGE_STRATEGY,
    "single_minded_message": MarketingPrinciple.MESSAGE_STRATEGY,
    "desired_reaction": MarketingPrinciple.MESSAGE_STRATEGY,
    "cta_strategy": MarketingPrinciple.CTA_STRATEGY,
}


class MarketingStrategyLLMOutput(BaseModel):
    """Strategy only. Copy, headlines, and art direction belong downstream."""

    model_config = ConfigDict(extra="forbid")

    business_objective: StrategicDecision
    segmentation: StrategicDecision
    targeting: StrategicDecision
    positioning: StrategicDecision
    customer_insight: StrategicDecision
    customer_tension: StrategicDecision
    usp: StrategicDecision
    value_proposition: StrategicDecision
    marketing_angle: StrategicDecision
    single_minded_message: StrategicDecision
    desired_reaction: StrategicDecision
    cta_strategy: StrategicDecision
    message_framework: MessageFrameworkChoice


class MarketingStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_objective: StrategicDecision
    segmentation: StrategicDecision
    targeting: StrategicDecision
    positioning: StrategicDecision
    customer_insight: StrategicDecision
    customer_tension: StrategicDecision
    usp: StrategicDecision
    value_proposition: StrategicDecision
    marketing_angle: StrategicDecision
    single_minded_message: StrategicDecision
    desired_reaction: StrategicDecision
    cta_strategy: StrategicDecision
    message_framework: MessageFrameworkChoice
    #: Named gaps in the evidence this strategy was built on, carried forward
    #: so a later stage inherits the uncertainty rather than the confidence.
    limitations: list[str] = Field(default_factory=list, max_length=20)
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    def decisions(self) -> dict[str, StrategicDecision]:
        return {name: getattr(self, name) for name in STRATEGY_DECISIONS}


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
    "DECISION_PRINCIPLES",
    "STRATEGY_DECISIONS",
    "MarketingPrinciple",
    "MarketingStrategy",
    "MarketingStrategyInput",
    "MarketingStrategyLLMOutput",
    "MessageFramework",
    "MessageFrameworkChoice",
    "StrategicDecision",
]
