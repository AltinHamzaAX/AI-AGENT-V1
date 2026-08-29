from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.modules.posts.domain.clarification import ClarificationPlan
from app.modules.posts.domain.enums import UnderstandingField
from app.shared.assets.domain import AssetRole


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation content cannot be blank")
        return normalized


class AttachmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    role: AssetRole
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=100)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "AttachmentContext":
        if (self.width is None) != (self.height is None):
            raise ValueError("attachment width and height must be provided together")
        return self


class ClientUnderstandingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=200)
    latest_message: str = Field(min_length=1, max_length=20_000)
    attachments: list[AttachmentContext] = Field(default_factory=list, max_length=50)
    project_context: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("latest_message")
    @classmethod
    def normalize_latest_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("latest_message cannot be blank")
        return normalized

    @model_validator(mode="after")
    def unique_attachment_ids(self) -> "ClientUnderstandingInput":
        identifiers = [attachment.id for attachment in self.attachments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("attachment IDs must be unique")
        return self


class UnderstoodAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    role: AssetRole
    original_filename: str
    preserve_identity: bool


class ClientUnderstandingBrief(BaseModel):
    """Facts understood from the client; deliberately contains no strategy fields."""

    model_config = ConfigDict(extra="forbid")

    business: str | None = Field(
        default=None,
        max_length=500,
        description="The named business or explicit business type, such as cafe or agency.",
    )
    brand: str | None = Field(default=None, max_length=500)
    product_service: str | None = Field(
        default=None,
        max_length=500,
        description="The product, service, venue, or subject being promoted.",
    )
    goal: str | None = Field(
        default=None,
        max_length=500,
        description="The requested business outcome, such as more visits, bookings, or sales.",
    )
    audience: str | None = Field(default=None, max_length=500)
    market: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=100)
    language: str | None = Field(
        default=None,
        max_length=100,
        description="Requested language, or the clear language of the latest client message.",
    )
    offer: str | None = Field(default=None, max_length=500)
    cta_intent: str | None = Field(
        default=None,
        max_length=500,
        description="Explicit audience action requested by the client; distinct from the goal.",
    )
    style_preferences: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    assets: list[UnderstoodAsset] = Field(default_factory=list, max_length=50)
    missing_fields: list[UnderstandingField] = Field(default_factory=list)
    #: The plan travels with the brief it was derived from: the Supervisor reads
    #: it to decide whether to stop for the client, and every later stage reads
    #: the same section back. Leaving it undeclared made this model reject the
    #: very section its own stage writes.
    clarification: ClarificationPlan | None = None


class ClientUnderstandingLLMOutput(BaseModel):
    """Provider output excludes assets and missing fields; both are deterministic."""

    model_config = ConfigDict(extra="forbid")

    business: str | None = Field(
        default=None,
        max_length=500,
        description="Named business or explicit business type.",
    )
    brand: str | None = Field(default=None, max_length=500)
    product_service: str | None = Field(
        default=None,
        max_length=500,
        description="Product, service, venue, or subject being promoted.",
    )
    goal: str | None = Field(
        default=None,
        max_length=500,
        description="Requested business outcome, e.g. more visits, bookings, or sales.",
    )
    audience: str | None = Field(default=None, max_length=500)
    market: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=100)
    language: str | None = Field(
        default=None,
        max_length=100,
        description="Requested language or clearly detected language of the latest message.",
    )
    offer: str | None = Field(default=None, max_length=500)
    cta_intent: str | None = Field(
        default=None,
        max_length=500,
        description="Explicit audience action; do not copy the business goal into this field.",
    )
    style_preferences: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Exact client quotes supporting extracted fields. Keys may be scalar field names, "
            "style_preferences, or constraints."
        ),
    )

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {field.value for field in UnderstandingField} | {
            "style_preferences",
            "constraints",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown evidence fields: {', '.join(unknown)}")
        normalized: dict[str, str] = {}
        for field_name, quote in value.items():
            clean_quote = quote.strip()
            if clean_quote:
                normalized[field_name] = clean_quote
        return normalized


__all__ = [
    "AttachmentContext",
    "ClientUnderstandingBrief",
    "ClientUnderstandingInput",
    "ClientUnderstandingLLMOutput",
    "ConversationTurn",
    "UnderstoodAsset",
    "UnderstandingField",
]
