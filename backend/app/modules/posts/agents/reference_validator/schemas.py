from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.creative import CreativeDNA, RepetitionMatch
from app.modules.posts.tools.research import ExternalResearchResult

REFERENCE_VALIDATOR_SCHEMA_VERSION = "1.1"
REFERENCE_QUALITY_THRESHOLD = 8


class ReferenceDimension(StrEnum):
    REFERENCE_FIT = "reference_fit"
    MARKET_FIT = "market_fit"
    BRAND_FIT = "brand_fit"
    CONCEPT_SIMILARITY = "concept_similarity"
    LAYOUT_SIMILARITY = "layout_similarity"
    VISUAL_PATTERN_SIMILARITY = "visual_pattern_similarity"
    DIFFERENTIATION = "differentiation"
    ORIGINALITY = "originality"


class ReferenceUse(StrEnum):
    LEARN_FROM = "LEARN_FROM"
    COPY = "COPY"


class ReferenceSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReferenceDecision(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"


class ReferenceValidatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, Any]
    brand: BrandAnalysis
    research: ExternalResearchResult
    marketing_strategy: MarketingStrategy
    creative_direction: CreativeDirection
    copy_draft: CopyDraft
    art_direction: ArtDirection
    design_spec: DesignSpec
    recent_creative_patterns: list[CreativeDNA] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def inputs_describe_one_post(self) -> "ReferenceValidatorInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("reference validator requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.brand.contract_fingerprint,
            self.research.contract_fingerprint,
            self.marketing_strategy.contract_fingerprint,
            self.creative_direction.contract_fingerprint,
            self.copy_draft.contract_fingerprint,
            self.art_direction.contract_fingerprint,
            self.design_spec.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("reference validator inputs disagree on the semantic contract")
        return self


class ReferenceDimensionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ReferenceDimension
    score: int = Field(ge=1, le=10)
    evidence: str = Field(min_length=1, max_length=1_000)

    @property
    def passed(self) -> bool:
        return self.score >= REFERENCE_QUALITY_THRESHOLD


class ReferenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_url: str = Field(min_length=1, max_length=2_000)
    classification: ReferenceUse
    learned_principles: list[str] = Field(default_factory=list, max_length=8)
    copied_specifics: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def classification_has_evidence(self) -> "ReferenceAssessment":
        if self.classification is ReferenceUse.COPY and not self.copied_specifics:
            raise ValueError("COPY classification requires copied specifics")
        if self.classification is ReferenceUse.LEARN_FROM and self.copied_specifics:
            raise ValueError("LEARN_FROM cannot contain copied specifics")
        return self


class ReferenceValidatorReadout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[ReferenceDimensionCheck] = Field(
        min_length=len(ReferenceDimension), max_length=len(ReferenceDimension)
    )
    references: list[ReferenceAssessment] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=1_500)

    @model_validator(mode="after")
    def checks_cover_every_dimension(self) -> "ReferenceValidatorReadout":
        dimensions = [check.dimension for check in self.checks]
        if set(dimensions) != set(ReferenceDimension) or len(dimensions) != len(set(dimensions)):
            raise ValueError("reference validator must check every dimension exactly once")
        urls = [item.reference_url for item in self.references]
        if len(urls) != len(set(urls)):
            raise ValueError("each reference may be assessed only once")
        return self


class ReferenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(min_length=1, max_length=600)
    region: str = Field(min_length=1, max_length=400)
    severity: ReferenceSeverity
    confidence: float = Field(ge=0, le=1)
    expected: str = Field(min_length=1, max_length=1_000)
    observed: str = Field(min_length=1, max_length=1_000)
    recommended_action: str = Field(min_length=1, max_length=1_000)
    dimensions: list[ReferenceDimension] = Field(min_length=1, max_length=8)


class GenericPatternSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, max_length=160)
    matched_elements: list[str] = Field(min_length=3, max_length=10)
    evidence: str = Field(min_length=1, max_length=1_000)


class ReferenceValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = REFERENCE_VALIDATOR_SCHEMA_VERSION
    decision: ReferenceDecision
    checks: list[ReferenceDimensionCheck] = Field(
        min_length=len(ReferenceDimension), max_length=len(ReferenceDimension)
    )
    references: list[ReferenceAssessment] = Field(default_factory=list, max_length=20)
    issues: list[ReferenceIssue] = Field(default_factory=list, max_length=20)
    generic_patterns: list[GenericPatternSignal] = Field(default_factory=list, max_length=10)
    creative_dna: CreativeDNA
    repetition_matches: list[RepetitionMatch] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=1_500)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    revision_requests: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def decision_matches_evidence(self) -> "ReferenceValidationReport":
        failed = [check for check in self.checks if not check.passed]
        copied = any(item.classification is ReferenceUse.COPY for item in self.references)
        expected = (
            ReferenceDecision.REVISE
            if failed or self.generic_patterns or copied
            else ReferenceDecision.PASS
        )
        if self.decision is not expected:
            raise ValueError("reference validation decision disagrees with its evidence")
        if (failed or copied) and not self.issues:
            raise ValueError("failed reference checks require actionable issues")
        if self.generic_patterns and not any(
            ReferenceDimension.ORIGINALITY in issue.dimensions
            or ReferenceDimension.DIFFERENTIATION in issue.dimensions
            for issue in self.issues
        ):
            raise ValueError("generic patterns require an originality issue")
        return self


__all__ = [
    "REFERENCE_QUALITY_THRESHOLD",
    "REFERENCE_VALIDATOR_SCHEMA_VERSION",
    "GenericPatternSignal",
    "ReferenceAssessment",
    "ReferenceDecision",
    "ReferenceDimension",
    "ReferenceDimensionCheck",
    "ReferenceIssue",
    "ReferenceSeverity",
    "ReferenceUse",
    "ReferenceValidationReport",
    "ReferenceValidatorInput",
    "ReferenceValidatorReadout",
]
