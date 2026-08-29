import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.generation import GenerationPlan

SCENE_PURITY_SCHEMA_VERSION = "1.0"


class ContaminationKind(StrEnum):
    """Everything a generated plate is forbidden to contain."""

    FAKE_TEXT = "fake_text"
    FAKE_LOGO = "fake_logo"
    WATERMARK = "watermark"
    AI_ARTIFACT = "ai_artifact"
    DUPLICATE_OBJECT = "duplicate_object"
    WRONG_PRODUCT = "wrong_product"
    UNEXPECTED_BRAND = "unexpected_brand"
    DISTORTION = "distortion"
    UNWANTED_UI = "unwanted_ui"


class ScenePurityVerdict(StrEnum):
    PASS = "PASS"
    REGENERATE_SCENE = "REGENERATE_SCENE"


class SceneObservation(BaseModel):
    """One contamination class as reported by the vision model.

    Confidence is the probability the contamination is *present*, never a
    verdict: the model reports, `policy.decide_scene_purity` decides.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ContaminationKind
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=400)

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: str) -> str:
        return _text(value)


class SceneReadout(BaseModel):
    """The vision model's structured description of the generated plate."""

    model_config = ConfigDict(extra="forbid")

    observations: list[SceneObservation] = Field(
        min_length=len(ContaminationKind), max_length=len(ContaminationKind)
    )
    #: Every legible string in the image. A plate carrying words is contaminated
    #: whatever the model concluded, so this list is evidence, not opinion.
    visible_text: list[str] = Field(default_factory=list, max_length=50)
    visible_brands: list[str] = Field(default_factory=list, max_length=25)
    depicted_products: list[str] = Field(default_factory=list, max_length=25)
    description: str = Field(min_length=1, max_length=2_000)

    @field_validator("visible_text", "visible_brands", "depicted_products", mode="after")
    @classmethod
    def normalize_entries(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        return [item for item in normalized if item]

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def observations_cover_every_kind(self) -> "SceneReadout":
        kinds = [observation.kind for observation in self.observations]
        if len(set(kinds)) != len(ContaminationKind):
            raise ValueError("scene readout must report every contamination kind exactly once")
        return self

    def confidence_for(self, kind: ContaminationKind) -> float:
        return next(item.confidence for item in self.observations if item.kind is kind)

    def evidence_for(self, kind: ContaminationKind) -> str:
        return next(item.evidence for item in self.observations if item.kind is kind)


class ScenePurityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContaminationKind
    confidence: float = Field(ge=0, le=1)
    detail: str = Field(min_length=1, max_length=600)


class ScenePurityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContaminationKind
    passed: bool
    detail: str = Field(min_length=1, max_length=600)


class ScenePurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scene_image: bytes = Field(min_length=1, exclude=True, repr=False)
    scene_mime_type: str = Field(pattern=r"^image/")
    scene_checksum: str = Field(min_length=64, max_length=64)
    scene_storage_key: str = Field(min_length=1, max_length=1_024)
    semantic_contract: dict[str, Any]
    generation_plan: GenerationPlan
    asset_policies: list[AssetPolicy] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def inputs_describe_one_inspected_scene(self) -> "ScenePurityInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("scene purity requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.generation_plan.contract_fingerprint,
            *(policy.contract_fingerprint for policy in self.asset_policies),
        }
        if len(fingerprints) != 1:
            raise ValueError("scene purity inputs disagree on the semantic contract")
        # Binds the verdict to the exact bytes inspected: a certificate for one
        # image can never be replayed over a different plate.
        if hashlib.sha256(self.scene_image).hexdigest() != self.scene_checksum:
            raise ValueError("scene bytes disagree with the recorded scene checksum")
        return self

    def contract(self) -> PostSemanticContract:
        return PostSemanticContract.from_dict(self.semantic_contract)


class ScenePurityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCENE_PURITY_SCHEMA_VERSION
    verdict: ScenePurityVerdict
    inspected: bool
    checks: list[ScenePurityCheck] = Field(default_factory=list, max_length=len(ContaminationKind))
    findings: list[ScenePurityFinding] = Field(
        default_factory=list, max_length=len(ContaminationKind)
    )
    scene_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    scene_storage_key: str | None = Field(default=None, max_length=1_024)
    regeneration_requests: int = Field(default=0, ge=0, le=10)
    reason: str = Field(min_length=1, max_length=1_000)
    provider: str | None = None
    model: str | None = None
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verdict_follows_the_findings(self) -> "ScenePurityReport":
        if self.inspected:
            kinds = [check.kind for check in self.checks]
            if len(set(kinds)) != len(ContaminationKind):
                raise ValueError("an inspected scene must carry one check per contamination kind")
            if self.scene_checksum is None or not self.scene_storage_key:
                raise ValueError("an inspected scene must identify the bytes it certifies")
            failed = {check.kind for check in self.checks if not check.passed}
            if failed != {finding.kind for finding in self.findings}:
                raise ValueError("scene purity findings and failed checks disagree")
        elif self.checks or self.findings or self.scene_checksum is not None:
            raise ValueError("an uninspected scene cannot carry checks, findings or a checksum")
        contaminated = bool(self.findings)
        expected = (
            ScenePurityVerdict.REGENERATE_SCENE if contaminated else ScenePurityVerdict.PASS
        )
        if self.verdict is not expected:
            raise ValueError("scene purity verdict disagrees with its findings")
        return self

    @classmethod
    def uninspected(cls, *, contract_fingerprint: str, reason: str) -> "ScenePurityReport":
        """A plate that was never generated cannot contaminate the composition."""
        return cls(
            verdict=ScenePurityVerdict.PASS,
            inspected=False,
            reason=reason,
            contract_fingerprint=contract_fingerprint,
        )

    def certifies(self, checksum: str) -> bool:
        """True when this report clears exactly the bytes identified by checksum."""
        return (
            self.verdict is ScenePurityVerdict.PASS
            and self.inspected
            and self.scene_checksum == checksum
        )


def _text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("scene purity text cannot be blank")
    return normalized


__all__ = [
    "SCENE_PURITY_SCHEMA_VERSION",
    "ContaminationKind",
    "SceneObservation",
    "SceneReadout",
    "ScenePurityCheck",
    "ScenePurityFinding",
    "ScenePurityInput",
    "ScenePurityReport",
    "ScenePurityVerdict",
]
