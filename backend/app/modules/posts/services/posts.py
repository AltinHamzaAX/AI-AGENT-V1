from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from app.modules.posts.domain.entities import (
    GenerationArtifact,
    Post,
    PostGeneration,
    PostScope,
)
from app.modules.posts.domain.enums import GenerationArtifactKind, GenerationStatus
from app.modules.posts.domain.exceptions import (
    PostGenerationNotFoundError,
    PostNotFoundError,
    PostSourceNotFoundError,
)
from app.modules.posts.repositories import PostRepository


class PostsService:
    def __init__(self, repository: PostRepository) -> None:
        self._repository = repository

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
    ) -> PostGeneration:
        generation = await self._repository.create_generation(post_id=post_id, scope=scope)
        if generation is None:
            raise PostNotFoundError
        return generation

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
