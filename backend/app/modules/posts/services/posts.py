from collections.abc import Sequence
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

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
from app.modules.posts.domain.exceptions import (
    PostGenerationNotFoundError,
    PostNotFoundError,
    PostSourceNotFoundError,
    SemanticContractHardFailError,
    SemanticContractNotFoundError,
    WorkflowStateConflictError,
)
from app.modules.posts.domain.jobs import GenerationJob
from app.modules.posts.domain.semantic_contract import (
    PostSemanticContract,
    SemanticAssertions,
    semantic_contract_violations,
)
from app.modules.posts.domain.state import (
    PostGenerationState,
    PostGenerationStateSnapshot,
    validate_section_value,
)
from app.modules.posts.repositories import PostRepository


class PostsService:
    def __init__(
        self,
        repository: PostRepository,
        *,
        generation_job_max_attempts: int = 3,
        generation_job_timeout_seconds: int = 900,
    ) -> None:
        self._repository = repository
        self._generation_job_max_attempts = generation_job_max_attempts
        self._generation_job_timeout_seconds = generation_job_timeout_seconds

    async def create_post(
        self,
        *,
        scope: PostScope,
        conversation_id: UUID | None,
        campaign_id: UUID | None,
        title: str | None,
    ) -> Post:
        normalized_title = title.strip() if title is not None else None
        normalized_title = normalized_title or None
        if normalized_title is not None and len(normalized_title) > 200:
            raise ValueError("Post title cannot exceed 200 characters")
        if conversation_id is not None and not await self._repository.conversation_exists(
            conversation_id=conversation_id,
            scope=scope,
        ):
            raise PostSourceNotFoundError
        return await self._repository.create_post(
            scope=scope,
            conversation_id=conversation_id,
            campaign_id=campaign_id,
            title=normalized_title,
        )

    async def get_post(self, *, post_id: UUID, scope: PostScope) -> Post:
        post = await self._repository.get_post(post_id=post_id, scope=scope)
        if post is None:
            raise PostNotFoundError
        return post

    async def request_generation(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
        idempotency_key: str | None = None,
    ) -> PostGeneration:
        request_key = idempotency_key.strip() if idempotency_key else uuid4().hex
        if len(request_key) > 200:
            raise ValueError("Idempotency-Key cannot exceed 200 characters")
        scoped_key = sha256(
            f"{scope.user_id}:{scope.project_id}:{post_id}:{request_key}".encode()
        ).hexdigest()
        generation = await self._repository.create_generation(
            post_id=post_id,
            scope=scope,
            idempotency_key=scoped_key,
            max_attempts=self._generation_job_max_attempts,
            timeout_seconds=self._generation_job_timeout_seconds,
        )
        if generation is None:
            raise PostNotFoundError
        return generation

    async def get_generation_job(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> GenerationJob:
        job = await self._repository.get_generation_job(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        if job is None:
            raise PostGenerationNotFoundError
        return job

    async def list_generations(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[PostGeneration]:
        generations = await self._repository.list_generations(post_id=post_id, scope=scope)
        if generations is None:
            raise PostNotFoundError
        return generations

    async def update_generation_status(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        status: GenerationStatus,
    ) -> PostGeneration:
        generation = await self._repository.update_generation_status(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            status=status,
        )
        if generation is None:
            raise PostGenerationNotFoundError
        return generation

    async def add_artifact(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        kind: GenerationArtifactKind,
        storage_key: str,
        mime_type: str,
        size_bytes: int,
        checksum: str,
        width: int | None = None,
        height: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationArtifact:
        if size_bytes <= 0:
            raise ValueError("Artifact size_bytes must be positive")
        if not storage_key.strip() or len(storage_key) > 1024:
            raise ValueError("Artifact storage_key must contain 1 to 1024 characters")
        if not mime_type.strip() or len(mime_type) > 100:
            raise ValueError("Artifact mime_type must contain 1 to 100 characters")
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("Artifact checksum must be a SHA-256 hex digest")
        if (width is None) != (height is None):
            raise ValueError("Artifact width and height must be provided together")
        if width is not None and (width <= 0 or height is None or height <= 0):
            raise ValueError("Artifact dimensions must be positive")
        artifact = await self._repository.add_artifact(
            artifact_id=uuid4(),
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            kind=kind,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum=checksum,
            width=width,
            height=height,
            metadata=metadata or {},
        )
        if artifact is None:
            raise PostGenerationNotFoundError
        return artifact

    async def list_artifacts(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[GenerationArtifact]:
        artifacts = await self._repository.list_artifacts(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        if artifacts is None:
            raise PostGenerationNotFoundError
        return artifacts

    async def get_workflow_state(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGenerationState:
        state = await self._repository.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        if state is None:
            raise PostGenerationNotFoundError
        return state

    async def write_workflow_section(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        section: PostWorkflowSection,
        value: Any,
        expected_version: int,
    ) -> PostGenerationState:
        if expected_version <= 0:
            raise ValueError("expected_version must be positive")
        if section is PostWorkflowSection.SEMANTIC_CONTRACT:
            raise SemanticContractHardFailError(
                ("semantic_contract is protected; use the dedicated contract operation",)
            )
        if section is PostWorkflowSection.SUPERVISOR:
            raise ValueError("supervisor state is internal and cannot be written through API")
        validated_value = validate_section_value(section, value)
        state = await self._repository.update_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            section=section,
            value=validated_value,
            expected_version=expected_version,
        )
        if state is not None:
            return state
        current = await self._repository.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        if current is None:
            raise PostGenerationNotFoundError
        raise WorkflowStateConflictError

    async def create_semantic_contract(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        contract: PostSemanticContract,
        expected_version: int,
    ) -> tuple[PostSemanticContract, PostGenerationState]:
        if expected_version <= 0:
            raise ValueError("expected_version must be positive")
        current = await self.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        existing = self._semantic_contract_from_state(current, required=False)
        if existing is not None:
            if existing.fingerprint == contract.fingerprint:
                return existing, current
            raise SemanticContractHardFailError(
                ("semantic_contract is immutable and cannot be replaced",)
            )
        if current.version != expected_version:
            raise WorkflowStateConflictError

        updated = await self._repository.update_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            section=PostWorkflowSection.SEMANTIC_CONTRACT,
            value=contract.to_dict(),
            expected_version=expected_version,
        )
        if updated is not None:
            return contract, updated

        latest = await self.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        concurrent = self._semantic_contract_from_state(latest, required=False)
        if concurrent is not None:
            if concurrent.fingerprint == contract.fingerprint:
                return concurrent, latest
            raise SemanticContractHardFailError(
                ("a different immutable semantic_contract already exists",)
            )
        raise WorkflowStateConflictError

    async def get_semantic_contract(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> tuple[PostSemanticContract, PostGenerationState]:
        state = await self.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        contract = self._semantic_contract_from_state(state, required=True)
        if contract is None:  # pragma: no cover - required=True raises instead
            raise SemanticContractNotFoundError
        return contract, state

    async def validate_semantic_assertions(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        assertions: SemanticAssertions,
    ) -> PostSemanticContract:
        contract, _ = await self.get_semantic_contract(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        violations = semantic_contract_violations(contract, assertions)
        if violations:
            raise SemanticContractHardFailError(violations)
        return contract

    async def list_workflow_state_versions(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[PostGenerationStateSnapshot]:
        versions = await self._repository.list_workflow_state_versions(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        if versions is None:
            raise PostGenerationNotFoundError
        return versions

    @staticmethod
    def _semantic_contract_from_state(
        state: PostGenerationState,
        *,
        required: bool,
    ) -> PostSemanticContract | None:
        value = state.data[PostWorkflowSection.SEMANTIC_CONTRACT.value]
        if not value:
            if required:
                raise SemanticContractNotFoundError
            return None
        try:
            return PostSemanticContract.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticContractHardFailError(
                ("persisted semantic_contract failed integrity validation",)
            ) from exc
