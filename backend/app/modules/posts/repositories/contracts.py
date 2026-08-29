from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from app.modules.posts.domain.chat import ConversationContext
from app.modules.posts.domain.entities import (
    GenerationArtifact,
    Post,
    PostGeneration,
    PostScope,
)
from app.modules.posts.domain.enums import (
    GenerationArtifactKind,
    GenerationStatus,
    PostWorkflowSection,
)
from app.modules.posts.domain.jobs import GenerationJob
from app.modules.posts.domain.memory import (
    SemanticMemory,
    SemanticMemoryKind,
    SemanticMemoryMatch,
    SemanticMemoryScope,
)
from app.modules.posts.domain.observability import ExecutionTrace
from app.modules.posts.domain.state import (
    PostGenerationState,
    PostGenerationStateSnapshot,
)


class PostRepository(Protocol):
    async def conversation_exists(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
    ) -> bool: ...

    async def create_post(
        self,
        *,
        scope: PostScope,
        conversation_id: UUID | None,
        campaign_id: UUID | None,
        title: str | None,
    ) -> Post: ...

    async def get_post(self, *, post_id: UUID, scope: PostScope) -> Post | None: ...

    async def find_post_by_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
    ) -> Post | None: ...

    async def create_generation(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
        idempotency_key: str,
        max_attempts: int,
        timeout_seconds: int,
    ) -> PostGeneration | None: ...

    async def get_generation_job(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> GenerationJob | None: ...

    async def get_generation(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGeneration | None: ...

    async def list_generations(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[PostGeneration] | None: ...

    async def update_generation_status(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        status: GenerationStatus,
    ) -> PostGeneration | None: ...

    async def add_artifact(
        self,
        *,
        artifact_id: UUID,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        kind: GenerationArtifactKind,
        storage_key: str,
        mime_type: str,
        size_bytes: int,
        checksum: str,
        width: int | None,
        height: int | None,
        metadata: dict[str, Any],
    ) -> GenerationArtifact | None: ...

    async def list_artifacts(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[GenerationArtifact] | None: ...

    async def list_execution_traces(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[ExecutionTrace] | None: ...

    async def get_workflow_state(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGenerationState | None: ...

    async def update_workflow_state(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        section: PostWorkflowSection,
        value: Any,
        expected_version: int,
    ) -> PostGenerationState | None: ...

    async def list_workflow_state_versions(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[PostGenerationStateSnapshot] | None: ...


class GenerationJobRepository(Protocol):
    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> GenerationJob | None: ...


class SemanticMemoryRepository(Protocol):
    @property
    def embedding_dimension(self) -> int: ...

    async def upsert(
        self,
        *,
        memory_id: UUID,
        scope: SemanticMemoryScope,
        kind: SemanticMemoryKind,
        content: str,
        content_hash: str,
        embedding: tuple[float, ...],
        embedding_provider: str,
        embedding_model: str,
        metadata: dict[str, Any],
    ) -> SemanticMemory: ...

    async def search(
        self,
        *,
        scope: SemanticMemoryScope,
        query_embedding: tuple[float, ...],
        kinds: tuple[SemanticMemoryKind, ...],
        limit: int,
        min_similarity: float,
    ) -> Sequence[SemanticMemoryMatch]: ...

    async def complete(self, *, job_id: UUID, worker_id: str) -> GenerationJob | None: ...

    async def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_delay_seconds: int,
    ) -> GenerationJob | None: ...


class PostConversationContextRepository(Protocol):
    async def get(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
    ) -> ConversationContext | None: ...

    async def save(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
        context: ConversationContext,
    ) -> ConversationContext: ...
