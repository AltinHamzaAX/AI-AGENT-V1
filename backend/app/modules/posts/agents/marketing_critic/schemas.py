import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.tools.composition import PostDraft

MARKETING_CRITIC_SCHEMA_VERSION = "1.0"
MARKETING_PASS_SCORE = 8


class MarketingDimension(StrEnum):
    OBJECTIVE_ALIGNMENT = "objective_alignment"
    AUDIENCE_RELEVANCE = "audience_relevance"
    POSITIONING = "positioning"
    MESSAGE_CLARITY = "message_clarity"
    USP_VALUE_PROPOSITION = "usp_value_proposition"
    SINGLE_MINDED_MESSAGE = "single_minded_message"
    CTA = "cta"
    STRATEGY_CONSISTENCY = "strategy_consistency"


class MarketingIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MarketingCriticDecision(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"


class MarketingCriticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    final_image: bytes = Field(min_length=1, exclude=True, repr=False)
    final_mime_type: str = Field(pattern=r"^image/")
    semantic_contract: dict[str, Any]
    strategy: MarketingStrategy
    copy_draft: CopyDraft
    post_draft: PostDraft

    @model_validator(mode="after")
    def inputs_describe_one_final_draft(self) -> "MarketingCriticInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("marketing critic requires a valid semantic contract") from exc
        if (
            len(
                {
                    contract.fingerprint,
                    self.strategy.contract_fingerprint,
                    self.copy_draft.contract_fingerprint,
                    self.post_draft.contract_fingerprint,
                }
            )
            != 1
        ):
            raise ValueError("marketing critic inputs disagree on the semantic contract")
        if hashlib.sha256(self.final_image).hexdigest() != self.post_draft.final_asset.checksum:
            raise ValueError("final render bytes disagree with the post draft checksum")
        return self


class MarketingDimensionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: MarketingDimension
    score: int = Field(ge=1, le=10)
    issue: str | None = Field(default=None, max_length=500)
    severity: MarketingIssueSeverity | None = None
    reason: str = Field(min_length=1, max_length=1_000)
    recommended_action: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def weak_scores_are_actionable(self) -> "MarketingDimensionReview":
        actionable = (
            self.issue is not None and self.severity is not None and self.recommended_action
        )
        if self.score < MARKETING_PASS_SCORE and not actionable:
            raise ValueError("a failing marketing score requires issue, severity and action")
        if self.score >= MARKETING_PASS_SCORE and any(
            value is not None for value in (self.issue, self.severity, self.recommended_action)
        ):
            raise ValueError("a passing marketing score cannot carry an issue")
        return self


class MarketingCriticReadout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: list[MarketingDimensionReview] = Field(
        min_length=len(MarketingDimension), max_length=len(MarketingDimension)
    )
    summary: str = Field(min_length=1, max_length=1_500)

    @model_validator(mode="after")
    def reviews_cover_every_dimension(self) -> "MarketingCriticReadout":
        dimensions = [review.dimension for review in self.reviews]
        if len(set(dimensions)) != len(MarketingDimension):
            raise ValueError("marketing critic must review every dimension exactly once")
        return self


class MarketingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: MarketingDimension
    issue: str = Field(min_length=1, max_length=500)
    severity: MarketingIssueSeverity
    reason: str = Field(min_length=1, max_length=1_000)
    recommended_action: str = Field(min_length=1, max_length=1_000)
    target_stage: SupervisorStage


class MarketingCriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = MARKETING_CRITIC_SCHEMA_VERSION
    decision: MarketingCriticDecision
    score: float = Field(ge=1, le=10)
    reviews: list[MarketingDimensionReview] = Field(
        min_length=len(MarketingDimension), max_length=len(MarketingDimension)
    )
    issues: list[MarketingIssue] = Field(default_factory=list, max_length=len(MarketingDimension))
    summary: str = Field(min_length=1, max_length=1_500)
    hard_fail: bool = False
    provider: str
    model: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)
    revision_requests: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def decision_matches_actionable_reviews(self) -> "MarketingCriticReport":
        failed = {
            review.dimension for review in self.reviews if review.score < MARKETING_PASS_SCORE
        }
        if failed != {issue.dimension for issue in self.issues}:
            raise ValueError("marketing critic issues disagree with failing reviews")
        expected = MarketingCriticDecision.REVISE if failed else MarketingCriticDecision.PASS
        if self.decision is not expected:
            raise ValueError("marketing critic decision disagrees with its issues")
        return self


__all__ = [
    "MARKETING_CRITIC_SCHEMA_VERSION",
    "MARKETING_PASS_SCORE",
    "MarketingCriticDecision",
    "MarketingCriticInput",
    "MarketingCriticReadout",
    "MarketingCriticReport",
    "MarketingDimension",
    "MarketingDimensionReview",
    "MarketingIssue",
    "MarketingIssueSeverity",
]
