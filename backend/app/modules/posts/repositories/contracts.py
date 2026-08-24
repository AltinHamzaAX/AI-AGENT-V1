from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from app.modules.posts.domain.entities import (
    GenerationArtifact,
    Post,
    PostGeneration,
    PostScope,
)
from app.modules.posts.domain.enums import GenerationArtifactKind, GenerationStatus


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

    async def create_generation(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGeneration | None: ...

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
