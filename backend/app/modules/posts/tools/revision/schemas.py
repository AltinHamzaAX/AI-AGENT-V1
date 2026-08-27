import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import SupervisorStage

REVISION_SCHEMA_VERSION = "1.0"


class RevisionRoute(StrEnum):
    COPY = "copy"
    TYPOGRAPHY = "typography"
    LAYOUT = "layout"
    COLOR = "color"
    SCENE = "scene"
    PRODUCT = "product"
    STRATEGY = "strategy"
    CONCEPT = "concept"


class RevisionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class RevisionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RevisionRoute
    why: str = Field(min_length=1, max_length=1_000)
    action: str = Field(min_length=1, max_length=1_000)
    location: str | None = Field(default=None, max_length=400)
    source: str = Field(min_length=1, max_length=160)


class RevisionInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = REVISION_SCHEMA_VERSION
    revision_id: str = Field(pattern=r"^rev_[0-9a-f]{16}$")
    iteration: int = Field(ge=1, le=20)
    status: RevisionStatus = RevisionStatus.PENDING
    route: RevisionRoute
    target_stage: SupervisorStage
    requested_by: SupervisorStage
    responsible_component: str = Field(min_length=1, max_length=120)
    keep: list[PostWorkflowSection] = Field(min_length=1, max_length=30)
    change: list[PostWorkflowSection] = Field(min_length=1, max_length=30)
    why: list[str] = Field(min_length=1, max_length=30)
    action: list[str] = Field(min_length=1, max_length=30)
    findings: list[RevisionFinding] = Field(min_length=1, max_length=30)
    render_reference: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def instruction_is_safe_and_complete(self) -> "RevisionInstruction":
        if len(set(self.keep)) != len(self.keep) or len(set(self.change)) != len(self.change):
            raise ValueError("revision keep/change scopes must be unique")
        overlap = set(self.keep).intersection(self.change)
        if overlap:
            raise ValueError("revision cannot keep and change the same workflow section")
        if self.route not in {finding.route for finding in self.findings}:
            raise ValueError("primary revision route must be represented by a finding")
        return self

    def signature(self) -> str:
        payload = {
            "route": self.route.value,
            "target_stage": self.target_stage.value,
            "requested_by": self.requested_by.value,
            "keep": [item.value for item in self.keep],
            "change": [item.value for item in self.change],
            "why": self.why,
            "action": self.action,
            "findings": [item.model_dump(mode="json") for item in self.findings],
            "render_reference": self.render_reference,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


__all__ = [
    "REVISION_SCHEMA_VERSION",
    "RevisionFinding",
    "RevisionInstruction",
    "RevisionRoute",
    "RevisionStatus",
]
