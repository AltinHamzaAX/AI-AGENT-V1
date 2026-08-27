from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.modules.posts.domain.entities import GenerationArtifact, Post, PostGeneration
from app.modules.posts.domain.enums import (
    GenerationArtifactKind,
    GenerationJobStatus,
    GenerationStatus,
    PostWorkflowSection,
)
from app.modules.posts.domain.jobs import GenerationJob
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    ExecutionTrace,
)
from app.modules.posts.domain.semantic_contract import (
    PROTECTED_SCALAR_FIELDS,
    PostSemanticContract,
    SemanticAssertions,
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
    job_id: UUID
    job_status: GenerationJobStatus
    deduplicated: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, generation: PostGeneration) -> Self:
        return cls(
            id=generation.id,
            post_id=generation.post_id,
            attempt=generation.attempt,
            status=generation.status,
            job_id=generation.job_id,
            job_status=generation.job_status,
            deduplicated=generation.deduplicated,
            created_at=generation.created_at,
            updated_at=generation.updated_at,
        )


class GenerationJobRead(BaseModel):
    id: UUID
    generation_id: UUID
    status: GenerationJobStatus
    attempts: int
    max_attempts: int
    timeout_seconds: int
    available_at: datetime
    leased_until: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, job: GenerationJob) -> Self:
        return cls(
            id=job.id,
            generation_id=job.generation_id,
            status=job.status,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            timeout_seconds=job.timeout_seconds,
            available_at=job.available_at,
            leased_until=job.leased_until,
            last_error_code=job.last_error_code,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
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


class ExecutionTraceRead(BaseModel):
    id: UUID
    generation_id: UUID
    correlation_id: UUID
    kind: ExecutionRunKind
    name: str
    status: ExecutionRunStatus
    input_reference: str | None
    output_reference: str | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    duration_ms: int
    retry_count: int
    error_code: str | None
    metadata: dict[str, JsonValue]
    started_at: datetime
    completed_at: datetime

    @classmethod
    def from_domain(cls, trace: ExecutionTrace) -> Self:
        return cls(
            id=trace.id,
            generation_id=trace.generation_id,
            correlation_id=trace.correlation_id,
            kind=trace.kind,
            name=trace.name,
            status=trace.status,
            input_reference=trace.input_reference,
            output_reference=trace.output_reference,
            provider=trace.provider,
            model=trace.model,
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            cost_usd=trace.cost_usd,
            duration_ms=trace.duration_ms,
            retry_count=trace.retry_count,
            error_code=trace.error_code,
            metadata=trace.metadata,
            started_at=trace.started_at,
            completed_at=trace.completed_at,
        )


class WorkflowStateData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    supervisor: dict[str, JsonValue]
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
    scene_purity: dict[str, JsonValue]
    post_draft: dict[str, JsonValue]
    verification: dict[str, JsonValue]
    quality: dict[str, JsonValue]
    design_quality: dict[str, JsonValue]
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


class SemanticContractFields(BaseModel):
    company: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=500)
    product: str | None = Field(default=None, max_length=500)
    primary_entity: str = Field(min_length=1, max_length=500)
    goal: str = Field(min_length=1, max_length=500)
    audience: str = Field(min_length=1, max_length=500)
    market: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    offer: str | None = Field(default=None, max_length=500)
    cta_intent: str = Field(min_length=1, max_length=500)
    platform: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=1, max_length=100)
    required_facts: dict[str, str] = Field(default_factory=dict, max_length=100)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=100)
    required_assets: list[UUID] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)

    def to_domain(self) -> PostSemanticContract:
        return PostSemanticContract.create(**self.model_dump())


class SemanticContractCreate(SemanticContractFields):
    expected_version: int = Field(ge=1)

    def to_domain(self) -> PostSemanticContract:
        return PostSemanticContract.create(
            **self.model_dump(exclude={"expected_version"}),
        )


class SemanticContractRead(SemanticContractFields):
    contract_version: int
    fingerprint: str

    @classmethod
    def from_domain(cls, contract: PostSemanticContract) -> Self:
        return cls.model_validate(contract.to_dict())


class SemanticContractStateRead(BaseModel):
    state_version: int
    contract: SemanticContractRead

    @classmethod
    def from_domain(
        cls,
        contract: PostSemanticContract,
        state: PostGenerationState,
    ) -> Self:
        return cls(
            state_version=state.version,
            contract=SemanticContractRead.from_domain(contract),
        )


class SemanticAssertionsRequest(BaseModel):
    contract_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    company: str | None = None
    brand: str | None = None
    product: str | None = None
    primary_entity: str | None = None
    goal: str | None = None
    audience: str | None = None
    market: str | None = None
    location: str | None = None
    offer: str | None = None
    cta_intent: str | None = None
    platform: str | None = None
    language: str | None = None
    required_facts: dict[str, str] = Field(default_factory=dict, max_length=100)
    claims: list[str] = Field(default_factory=list, max_length=100)
    used_assets: list[UUID] | None = Field(default=None, max_length=100)

    def to_domain(self) -> SemanticAssertions:
        protected_values = {
            field_name: getattr(self, field_name)
            for field_name in PROTECTED_SCALAR_FIELDS
            if field_name in self.model_fields_set
        }
        return SemanticAssertions(
            contract_fingerprint=self.contract_fingerprint,
            protected_values=protected_values,
            required_facts=self.required_facts,
            claims=tuple(self.claims),
            used_assets=tuple(self.used_assets) if self.used_assets is not None else None,
        )


class SemanticValidationRead(BaseModel):
    valid: bool = True
    decision: str = "CONTINUE"
    fingerprint: str
