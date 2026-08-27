import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.composition import PostDraft

VERIFICATION_SCHEMA_VERSION = "1.0"


class VerificationGate(StrEnum):
    """Every gate here is hard: one failure blocks the post, whatever it scored.

    They divide into what the post must get right (`correct_*`), what it must
    carry (`required_*`), and what it must not contain (`*_absent`). None of them
    is a matter of degree, so none of them has a threshold to argue with.
    """

    CORRECT_BRAND = "correct_brand"
    CORRECT_PRODUCT = "correct_product"
    CORRECT_LOGO = "correct_logo"
    CORRECT_OFFER = "correct_offer"
    CORRECT_SPELLING = "correct_spelling"
    REQUIRED_FACTS_PRESENT = "required_facts_present"
    REQUIRED_ASSETS_PRESENT = "required_assets_present"
    FORBIDDEN_CLAIMS_ABSENT = "forbidden_claims_absent"
    FAKE_BRANDING_ABSENT = "fake_branding_absent"
    UNWANTED_TEXT_ABSENT = "unwanted_text_absent"
    CORRECT_DIMENSIONS = "correct_dimensions"
    ASSET_FIDELITY = "asset_fidelity"


class VerificationDecision(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class RenderReadout(BaseModel):
    """What the vision model saw in the final render, as evidence not judgement.

    The witness enumerates; `policy.decide_verification` decides. Nothing here
    is an opinion about whether the post is acceptable, so a model that is bad
    at judging can still be useful at looking.
    """

    model_config = ConfigDict(extra="forbid")

    #: Every legible string, copied exactly. Approved copy is expected here; a
    #: string that belongs to no approved component is what the gate is for.
    visible_text: list[str] = Field(default_factory=list, max_length=60)
    #: Every brand, wordmark or emblem identity the model recognises.
    visible_brands: list[str] = Field(default_factory=list, max_length=25)
    #: Every manufactured product shown as a subject.
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
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("render description cannot be blank")
        return normalized


#: The readout as constrained decoding. A vision model asked in prose for this
#: object spends most of its output on private reasoning; a grammar leaves it
#: nowhere to put anything but the answer.
RENDER_READOUT_WIRE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "visible_text": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
        "visible_brands": {"type": "array", "items": {"type": "string"}, "maxItems": 25},
        "depicted_products": {"type": "array", "items": {"type": "string"}, "maxItems": 25},
        "description": {"type": "string"},
    },
    "required": ["visible_text", "visible_brands", "depicted_products", "description"],
}


class GateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: VerificationGate
    passed: bool
    detail: str = Field(min_length=1, max_length=600)


class GateFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: VerificationGate
    detail: str = Field(min_length=1, max_length=600)
    #: What the failure was read off, so a blocked post can be argued with.
    evidence: list[str] = Field(default_factory=list, max_length=20)


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    final_image: bytes = Field(min_length=1, exclude=True, repr=False)
    final_mime_type: str = Field(pattern=r"^image/")
    semantic_contract: dict[str, Any]
    copy_draft: CopyDraft
    design_spec: DesignSpec
    post_draft: PostDraft
    asset_policies: list[AssetPolicy] = Field(default_factory=list, max_length=50)
    #: Legal wording comes from the composer input rather than the copywriter, so
    #: it is approved text the copy draft cannot account for.
    legal_text: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def inputs_describe_one_render(self) -> "VerificationInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("verification requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.copy_draft.contract_fingerprint,
            self.design_spec.contract_fingerprint,
            self.post_draft.contract_fingerprint,
            *(policy.contract_fingerprint for policy in self.asset_policies),
        }
        if len(fingerprints) != 1:
            raise ValueError("verification inputs disagree on the semantic contract")
        # Binds the verdict to the exact bytes verified: a certificate for one
        # render can never be replayed over a different one.
        if hashlib.sha256(self.final_image).hexdigest() != self.post_draft.final_asset.checksum:
            raise ValueError("final render bytes disagree with the post draft checksum")
        return self

    def contract(self) -> PostSemanticContract:
        return PostSemanticContract.from_dict(self.semantic_contract)


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = VERIFICATION_SCHEMA_VERSION
    decision: VerificationDecision
    checks: list[GateCheck] = Field(
        min_length=len(VerificationGate), max_length=len(VerificationGate)
    )
    failures: list[GateFailure] = Field(default_factory=list, max_length=len(VerificationGate))
    reason: str = Field(min_length=1, max_length=1_000)
    render_checksum: str = Field(min_length=64, max_length=64)
    render_fingerprint: str = Field(min_length=64, max_length=64)
    contract_fingerprint: str = Field(min_length=64, max_length=64)
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def decision_follows_the_gates(self) -> "VerificationReport":
        gates = [check.gate for check in self.checks]
        if len(set(gates)) != len(VerificationGate):
            raise ValueError("verification must report every gate exactly once")
        failed = {check.gate for check in self.checks if not check.passed}
        if failed != {failure.gate for failure in self.failures}:
            raise ValueError("verification failures disagree with failed gates")
        # The whole point of the layer: no score, no average, no override. One
        # failed gate is the decision.
        expected = VerificationDecision.BLOCKED if failed else VerificationDecision.PASS
        if self.decision is not expected:
            raise ValueError("verification decision disagrees with its gates")
        return self

    def certifies(self, checksum: str) -> bool:
        """True when this report clears exactly the bytes identified by checksum."""
        return self.decision is VerificationDecision.PASS and self.render_checksum == checksum


__all__ = [
    "RENDER_READOUT_WIRE_SCHEMA",
    "VERIFICATION_SCHEMA_VERSION",
    "GateCheck",
    "GateFailure",
    "RenderReadout",
    "VerificationDecision",
    "VerificationGate",
    "VerificationInput",
    "VerificationReport",
]
