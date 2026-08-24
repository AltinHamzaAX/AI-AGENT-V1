from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.modules.posts.domain.enums import (
    GenerationArtifactKind,
    GenerationJobStatus,
    GenerationStatus,
)


@dataclass(frozen=True, slots=True)
class PostScope:
    user_id: UUID
    project_id: UUID


@dataclass(frozen=True, slots=True)
class Post:
    id: UUID
    scope: PostScope
    conversation_id: UUID | None
    campaign_id: UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PostGeneration:
    id: UUID
    post_id: UUID
    attempt: int
    status: GenerationStatus
    job_id: UUID
    job_status: GenerationJobStatus
    deduplicated: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationArtifact:
    id: UUID
    generation_id: UUID
    kind: GenerationArtifactKind
    storage_key: str
    mime_type: str
    size_bytes: int
    checksum: str
    created_at: datetime
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["GenerationArtifact", "Post", "PostGeneration", "PostScope"]
