import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.tools.composition import PostDraft

DESIGN_CRITIC_SCHEMA_VERSION = "1.0"


class DesignDimension(StrEnum):
    COMPOSITION = "composition"
    HIERARCHY = "hierarchy"
    TYPOGRAPHY = "typography"
    SPACING = "spacing"
    ALIGNMENT = "alignment"
    COLOR = "color"
    CONTRAST = "contrast"
    BALANCE = "balance"
    FOCAL_POINT = "focal_point"
    NEGATIVE_SPACE = "negative_space"
    BRAND_CONSISTENCY = "brand_consistency"
    PRODUCT_DOMINANCE = "product_dominance"
    CREATIVITY = "creativity"
    PLATFORM_FIT = "platform_fit"
    MOBILE_READABILITY = "mobile_readability"
    POLISH = "polish"


class DesignIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DesignCriticDecision(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"


class DesignCriticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    final_image: bytes = Field(min_length=1, exclude=True, repr=False)
    final_mime_type: str = Field(pattern=r"^image/")
    semantic_contract: dict[str, Any]
    art_direction: ArtDirection
    design_spec: DesignSpec
    post_draft: PostDraft

    @model_validator(mode="after")
    def inputs_describe_one_render(self) -> "DesignCriticInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("design critic requires a valid semantic contract") from exc
        if (
            len(
                {
                    contract.fingerprint,
                    self.art_direction.contract_fingerprint,
                    self.design_spec.contract_fingerprint,
                    self.post_draft.contract_fingerprint,
                }
            )
            != 1
        ):
            raise ValueError("design critic inputs disagree on the semantic contract")
        if hashlib.sha256(self.final_image).hexdigest() != self.post_draft.final_asset.checksum:
            raise ValueError("final render bytes disagree with the post draft checksum")
        return self


class DesignDimensionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DesignDimension
    passed: bool
    problem: str | None = Field(default=None, max_length=600)
    location: str | None = Field(default=None, max_length=400)
    cause: str | None = Field(default=None, max_length=1_000)
    severity: DesignIssueSeverity | None = None
    recommended_change: str | None = Field(default=None, max_length=1_000)
    evidence: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def diagnosis_matches_result(self) -> "DesignDimensionCheck":
        diagnosis = (
            self.problem,
            self.location,
            self.cause,
            self.severity,
            self.recommended_change,
        )
        if not self.passed and any(value is None for value in diagnosis):
            raise ValueError(
                "a failed design check requires problem, location, cause, severity and change"
            )
        if self.passed and any(value is not None for value in diagnosis):
            raise ValueError("a passing design check cannot carry a problem diagnosis")
        return self


class DesignCriticReadout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[DesignDimensionCheck] = Field(
        min_length=len(DesignDimension), max_length=len(DesignDimension)
    )
    summary: str = Field(min_length=1, max_length=1_500)

    @model_validator(mode="after")
    def checks_cover_every_dimension(self) -> "DesignCriticReadout":
        dimensions = [check.dimension for check in self.checks]
        if len(set(dimensions)) != len(DesignDimension):
            raise ValueError("design critic must check every dimension exactly once")
        return self


def _wire_schema() -> dict[str, Any]:
    """The review shape as constrained decoding, not as prompt instructions.

    A vision model asked in prose for this object spends most of its output on
    private reasoning and still omits diagnosis fields on the checks it fails.
    Splitting the check into a passing and a failing variant makes the five
    required diagnosis fields structurally unskippable, so a failed review
    arrives complete on the first call instead of after a repair pass.
    """
    dimension = {"enum": [item.value for item in DesignDimension]}
    evidence = {"type": "string"}
    passing = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dimension": dimension,
            "passed": {"const": True},
            "evidence": evidence,
        },
        "required": ["dimension", "passed", "evidence"],
    }
    failing = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dimension": dimension,
            "passed": {"const": False},
            "problem": {"type": "string"},
            "location": {"type": "string"},
            "cause": {"type": "string"},
            "severity": {"enum": [item.value for item in DesignIssueSeverity]},
            "recommended_change": {"type": "string"},
            "evidence": evidence,
        },
        "required": [
            "dimension",
            "passed",
            "problem",
            "location",
            "cause",
            "severity",
            "recommended_change",
            "evidence",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checks": {
                "type": "array",
                "items": {"anyOf": [passing, failing]},
                "minItems": len(DesignDimension),
                "maxItems": len(DesignDimension),
            },
            "summary": {"type": "string"},
        },
        "required": ["checks", "summary"],
    }


DESIGN_CRITIC_WIRE_SCHEMA = _wire_schema()


class DesignProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DesignDimension
    problem: str = Field(min_length=1, max_length=600)
    location: str = Field(min_length=1, max_length=400)
    cause: str = Field(min_length=1, max_length=1_000)
    severity: DesignIssueSeverity
    recommended_change: str = Field(min_length=1, max_length=1_000)
    target_stage: SupervisorStage


class DesignCriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = DESIGN_CRITIC_SCHEMA_VERSION
    decision: DesignCriticDecision
    checks: list[DesignDimensionCheck] = Field(
        min_length=len(DesignDimension), max_length=len(DesignDimension)
    )
    problems: list[DesignProblem] = Field(default_factory=list, max_length=len(DesignDimension))
    summary: str = Field(min_length=1, max_length=1_500)
    provider: str
    model: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)
    revision_requests: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def decision_matches_diagnosed_problems(self) -> "DesignCriticReport":
        failed = {check.dimension for check in self.checks if not check.passed}
        if failed != {problem.dimension for problem in self.problems}:
            raise ValueError("design problems disagree with failed checks")
        expected = DesignCriticDecision.REVISE if failed else DesignCriticDecision.PASS
        if self.decision is not expected:
            raise ValueError("design critic decision disagrees with its problems")
        return self


__all__ = [
    "DESIGN_CRITIC_SCHEMA_VERSION",
    "DESIGN_CRITIC_WIRE_SCHEMA",
    "DesignCriticDecision",
    "DesignCriticInput",
    "DesignCriticReadout",
    "DesignCriticReport",
    "DesignDimension",
    "DesignDimensionCheck",
    "DesignIssueSeverity",
    "DesignProblem",
]
