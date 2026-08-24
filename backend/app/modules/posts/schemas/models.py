from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from app.modules.posts.domain.entities import GenerationArtifact, Post, PostGeneration
from app.modules.posts.domain.enums import GenerationArtifactKind, GenerationStatus


class PostCreate(BaseModel):
    conversation_id: UUID | None = None
    campaign_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PostRead(BaseModel):
    id: UUID
    project_id: UUID
    conversation_id: UUID | None
    campaign_id: UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, post: Post) -> Self:
        return cls(
            id=post.id,
            project_id=post.scope.project_id,
            conversation_id=post.conversation_id,
            campaign_id=post.campaign_id,
            title=post.title,
            created_at=post.created_at,
            updated_at=post.updated_at,
        )


class PostGenerationRead(BaseModel):
    id: UUID
    post_id: UUID
    attempt: int
    status: GenerationStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, generation: PostGeneration) -> Self:
        return cls(
            id=generation.id,
            post_id=generation.post_id,
            attempt=generation.attempt,
            status=generation.status,
            created_at=generation.created_at,
            updated_at=generation.updated_at,
        )


class GenerationArtifactRead(BaseModel):
    id: UUID
    generation_id: UUID
    kind: GenerationArtifactKind
    mime_type: str
    size_bytes: int
    checksum: str
    width: int | None
    height: int | None
    metadata: dict[str, JsonValue]
    created_at: datetime

    @classmethod
    def from_domain(cls, artifact: GenerationArtifact) -> Self:
        return cls(
            id=artifact.id,
            generation_id=artifact.generation_id,
            kind=artifact.kind,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            checksum=artifact.checksum,
            width=artifact.width,
            height=artifact.height,
            metadata=artifact.metadata,
            created_at=artifact.created_at,
        )
