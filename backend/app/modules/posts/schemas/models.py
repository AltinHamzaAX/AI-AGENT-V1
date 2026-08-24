from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.modules.posts.domain.entities import GenerationArtifact, Post, PostGeneration
from app.modules.posts.domain.enums import (
    GenerationArtifactKind,
    GenerationStatus,
    PostWorkflowSection,
)
from app.modules.posts.domain.state import (
    PostGenerationState,
    PostGenerationStateSnapshot,
)


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


class WorkflowStateData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_context: dict[str, JsonValue]
    brief: dict[str, JsonValue]
    semantic_contract: dict[str, JsonValue]
    brand: dict[str, JsonValue]
    product: dict[str, JsonValue]
    assets: list[JsonValue]
    audience: dict[str, JsonValue]
    research: dict[str, JsonValue]
    marketing_strategy: dict[str, JsonValue]
    creative_concept: dict[str, JsonValue]
    copy_data: dict[str, JsonValue] = Field(alias="copy")
    art_direction: dict[str, JsonValue]
    design_spec: dict[str, JsonValue]
    generation_plan: dict[str, JsonValue]
    generation_artifacts: list[JsonValue]
    quality: dict[str, JsonValue]
    revision_history: list[JsonValue]


class WorkflowSectionWrite(BaseModel):
    expected_version: int = Field(ge=1)
    value: JsonValue


class PostGenerationStateRead(BaseModel):
    generation_id: UUID
    schema_version: int
    version: int
    state: WorkflowStateData
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, workflow_state: PostGenerationState) -> Self:
        return cls(
            generation_id=workflow_state.generation_id,
            schema_version=workflow_state.schema_version,
            version=workflow_state.version,
            state=WorkflowStateData.model_validate(workflow_state.data),
            created_at=workflow_state.created_at,
            updated_at=workflow_state.updated_at,
        )


class PostGenerationStateVersionRead(BaseModel):
    generation_id: UUID
    version: int
    schema_version: int
    changed_section: PostWorkflowSection | None
    state: WorkflowStateData
    created_at: datetime

    @classmethod
    def from_domain(
        cls,
        snapshot: PostGenerationStateSnapshot,
    ) -> Self:
        return cls(
            generation_id=snapshot.generation_id,
            version=snapshot.version,
            schema_version=snapshot.schema_version,
            changed_section=snapshot.changed_section,
            state=WorkflowStateData.model_validate(snapshot.data),
            created_at=snapshot.created_at,
        )
