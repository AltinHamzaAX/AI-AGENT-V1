from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ConversationScope:
    user_id: UUID
    project_id: UUID


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    scope: ConversationScope
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    conversation_id: UUID
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: tuple[Message, ...]
    total: int
    offset: int
    limit: int


class ConversationNotFoundError(LookupError):
    pass
