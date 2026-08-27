from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

QUALITY_SCORE_SCHEMA_VERSION = "1.0"


class QualityDimension(StrEnum):
    MARKETING_EFFECTIVENESS = "marketing_effectiveness"
    CREATIVE_CONCEPT = "creative_concept"
    COMPOSITION = "composition"
    VISUAL_HIERARCHY = "visual_hierarchy"
    TYPOGRAPHY = "typography"
    COLOR = "color"
    BRAND_FIT = "brand_fit"
    PRODUCT_FIDELITY = "product_fidelity"
    AUDIENCE_FIT = "audience_fit"
    PLATFORM_FIT = "platform_fit"
    DIFFERENTIATION = "differentiation"
    OVERALL_POLISH = "overall_polish"


class ApprovalDecision(StrEnum):
    PASS = "PASS"
    MUTATE = "MUTATE"
    RECOMPOSE = "RECOMPOSE"
    REGENERATE = "REGENERATE"
    REJECT = "REJECT"


DEFAULT_CRITICAL_DIMENSIONS = frozenset(
    {
        QualityDimension.MARKETING_EFFECTIVENESS,
        QualityDimension.BRAND_FIT,
        QualityDimension.PRODUCT_FIDELITY,
        QualityDimension.AUDIENCE_FIT,
    }
)


class QualityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_minimum: float = Field(default=9.0, ge=1, le=10)
    critical_minimum: float = Field(default=8.5, ge=1, le=10)
    dimension_minimum: float = Field(default=8.0, ge=1, le=10)
    critical_dimensions: frozenset[QualityDimension] = DEFAULT_CRITICAL_DIMENSIONS
    weights: dict[QualityDimension, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def thresholds_are_coherent(self) -> "QualityThresholds":
        if self.critical_minimum < self.dimension_minimum:
            raise ValueError("critical_minimum cannot be below dimension_minimum")
        unknown = set(self.weights) - set(QualityDimension)
        if unknown or any(weight <= 0 for weight in self.weights.values()):
            raise ValueError("quality weights must be positive known dimensions")
        if not self.critical_dimensions:
            raise ValueError("at least one critical dimension is required")
        return self

    def weight_for(self, dimension: QualityDimension) -> float:
        return self.weights.get(dimension, 1.0)


class QualityScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: QualityDimension
    score: float = Field(ge=1, le=10)
    threshold: float = Field(ge=1, le=10)
    critical: bool
    passed: bool
    evidence: list[str] = Field(min_length=1, max_length=12)
    source: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def pass_flag_matches_threshold(self) -> "QualityScore":
        if self.passed != (self.score >= self.threshold):
            raise ValueError("quality score pass flag disagrees with threshold")
        return self


class QualityScoringInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketing_report: dict
    design_report: dict
    creative_direction: dict
    verification_report: dict
    render_checksum: str = Field(min_length=64, max_length=64)
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    thresholds: QualityThresholds = Field(default_factory=QualityThresholds)


class QualityApprovalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = QUALITY_SCORE_SCHEMA_VERSION
    decision: ApprovalDecision
    overall_score: float = Field(ge=1, le=10)
    scores: list[QualityScore] = Field(
        min_length=len(QualityDimension), max_length=len(QualityDimension)
    )
    failed_dimensions: list[QualityDimension] = Field(default_factory=list)
    failed_hard_gates: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=1_500)
    recommended_action: str | None = Field(default=None, max_length=1_000)
    thresholds: QualityThresholds
    render_checksum: str = Field(min_length=64, max_length=64)
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def report_is_self_consistent(self) -> "QualityApprovalReport":
        dimensions = [item.dimension for item in self.scores]
        if len(set(dimensions)) != len(QualityDimension):
            raise ValueError("quality report must score every dimension exactly once")
        failed = [item.dimension for item in self.scores if not item.passed]
        if failed != self.failed_dimensions:
            raise ValueError("failed dimensions disagree with dimension scores")
        if self.decision is ApprovalDecision.PASS and (
            failed or self.failed_hard_gates or self.overall_score < self.thresholds.overall_minimum
        ):
            raise ValueError("PASS requires every threshold and hard gate to pass")
        if self.decision is not ApprovalDecision.PASS and not self.recommended_action:
            raise ValueError("a non-PASS decision requires a recommended action")
        return self


__all__ = [
    "ApprovalDecision",
    "QUALITY_SCORE_SCHEMA_VERSION",
    "QualityApprovalReport",
    "QualityDimension",
    "QualityScore",
    "QualityScoringInput",
    "QualityThresholds",
]
