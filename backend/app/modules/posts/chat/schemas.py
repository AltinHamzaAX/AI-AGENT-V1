from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.posts.domain.chat import ChatIntent, ContextUpdate


class ConversationRouterOutput(BaseModel):
    """What the classifier is allowed to return for one client message."""

    model_config = ConfigDict(extra="forbid")

    intent: ChatIntent
    reason: str = Field(default="", max_length=400)
    context_updates: ContextUpdate = Field(default_factory=ContextUpdate)
    revision_instructions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return " ".join(value.split())[:400]

    @field_validator("revision_instructions")
    @classmethod
    def normalize_instructions(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:10]


__all__ = ["ConversationRouterOutput"]
