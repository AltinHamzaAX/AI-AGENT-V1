import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.composition import PostDraft

VISION_CRITIC_SCHEMA_VERSION = "1.0"


class VisionDimension(StrEnum):
    VISUAL_HIERARCHY = "visual_hierarchy"
    READABILITY = "readability"
    PRODUCT_FIDELITY = "product_fidelity"
    LOGO_APPEARANCE = "logo_appearance"
    CROPS = "crops"
    OVERLAPS = "overlaps"
    SPACING = "spacing"
    DISTORTION = "distortion"
    AI_ARTIFACTS = "ai_artifacts"
    VISUAL_BALANCE = "visual_balance"
    FOCAL_POINT = "focal_point"
    SUBJECT_SCALE = "subject_scale"
    CTA_VISIBILITY = "cta_visibility"
    TEXT_LEGIBILITY = "text_legibility"


class VisionIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VisionCriticDecision(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"


class VisionCriticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    final_image: bytes = Field(min_length=1, exclude=True, repr=False)
    final_mime_type: str = Field(pattern=r"^image/")
    semantic_contract: dict[str, Any]
    copy_draft: CopyDraft
    design_spec: DesignSpec
    post_draft: PostDraft
    asset_policies: list[AssetPolicy] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def inputs_describe_one_render(self) -> "VisionCriticInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("vision critic requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.copy_draft.contract_fingerprint,
            self.design_spec.contract_fingerprint,
            self.post_draft.contract_fingerprint,
            *(policy.contract_fingerprint for policy in self.asset_policies),
        }
        if len(fingerprints) != 1:
            raise ValueError("vision critic inputs disagree on the semantic contract")
        if hashlib.sha256(self.final_image).hexdigest() != self.post_draft.final_asset.checksum:
            raise ValueError("final render bytes disagree with the post draft checksum")
        return self


class VisionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: VisionDimension
    issue: str = Field(min_length=1, max_length=600)
    region: str = Field(min_length=1, max_length=400)
    severity: VisionIssueSeverity
    confidence: float = Field(ge=0, le=1)
    expected: str = Field(min_length=1, max_length=800)
    observed: str = Field(min_length=1, max_length=800)
    recommended_action: str = Field(min_length=1, max_length=1_000)


class VisionCriticReadout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessed_dimensions: list[VisionDimension] = Field(
        min_length=len(VisionDimension), max_length=len(VisionDimension)
    )
    issues: list[VisionIssue] = Field(default_factory=list, max_length=40)
    summary: str = Field(min_length=1, max_length=1_500)

    @model_validator(mode="after")
    def covers_every_dimension_once(self) -> "VisionCriticReadout":
        if len(set(self.assessed_dimensions)) != len(VisionDimension):
            raise ValueError("vision critic must assess every dimension exactly once")
        return self


VISION_CRITIC_WIRE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessed_dimensions": {
            "type": "array",
            "items": {"enum": [item.value for item in VisionDimension]},
            "minItems": len(VisionDimension),
            "maxItems": len(VisionDimension),
        },
        "issues": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "dimension": {"enum": [item.value for item in VisionDimension]},
                    "issue": {"type": "string"},
                    "region": {"type": "string"},
                    "severity": {"enum": [item.value for item in VisionIssueSeverity]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "expected": {"type": "string"},
                    "observed": {"type": "string"},
                    "recommended_action": {"type": "string"},
                },
                "required": [
                    "dimension", "issue", "region", "severity", "confidence",
                    "expected", "observed", "recommended_action",
                ],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["assessed_dimensions", "issues", "summary"],
}


class VisionCriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = VISION_CRITIC_SCHEMA_VERSION
    decision: VisionCriticDecision
    assessed_dimensions: list[VisionDimension]
    issues: list[VisionIssue] = Field(default_factory=list, max_length=40)
    summary: str = Field(min_length=1, max_length=1_500)
    provider: str
    model: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)
    render_checksum: str = Field(min_length=64, max_length=64)
    revision_requests: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def decision_matches_issues(self) -> "VisionCriticReport":
        expected = VisionCriticDecision.REVISE if self.issues else VisionCriticDecision.PASS
        if self.decision is not expected:
            raise ValueError("vision critic decision disagrees with its issues")
        return self


__all__ = [
    "VISION_CRITIC_SCHEMA_VERSION", "VISION_CRITIC_WIRE_SCHEMA", "VisionCriticDecision",
    "VisionCriticInput", "VisionCriticReadout", "VisionCriticReport", "VisionDimension",
    "VisionIssue", "VisionIssueSeverity",
]
