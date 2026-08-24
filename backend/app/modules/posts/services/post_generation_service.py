from typing import Protocol
from uuid import UUID

from app.modules.posts.domain.entities import Post, PostGeneration, PostScope


class PostGenerationService(Protocol):
    """Public Posts boundary used by HTTP and future Campaigns callers."""

    async def create_post(
        self,
        *,
        scope: PostScope,
        conversation_id: UUID | None,
        campaign_id: UUID | None,
        title: str | None,
    ) -> Post: ...

    async def request_generation(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
        idempotency_key: str | None = None,
    ) -> PostGeneration: ...
