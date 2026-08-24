from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from app.shared.conversations.domain import Conversation, Message, MessagePage, MessageRole


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConversationRead(BaseModel):
    id: UUID
    project_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, conversation: Conversation) -> Self:
        return cls(
            id=conversation.id,
            project_id=conversation.scope.project_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )


class MessageCreate(BaseModel):
    role: MessageRole = MessageRole.USER
    content: str = Field(min_length=1, max_length=50_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content cannot be blank")
        return value


class MessageRead(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence: int
    role: MessageRole
    content: str
    metadata: dict[str, JsonValue]
    created_at: datetime

    @classmethod
    def from_domain(cls, message: Message) -> Self:
        return cls(
            id=message.id,
            conversation_id=message.conversation_id,
            sequence=message.sequence,
            role=message.role,
            content=message.content,
            metadata=message.metadata,
            created_at=message.created_at,
        )


class MessagePageRead(BaseModel):
    items: list[MessageRead]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_domain(cls, page: MessagePage) -> Self:
        return cls(
            items=[MessageRead.from_domain(message) for message in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )
