from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posts import PostModel
from app.modules.posts.domain.memory import (
    SemanticMemoryScope,
    SemanticMemoryScopeLevel,
)


class SQLAlchemyPostMemoryScopeResolver:
    """Resolves a trusted worker post ID to its tenant-safe project partition."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_project_scope(self, *, post_id: UUID) -> SemanticMemoryScope | None:
        row = (
            await self._session.execute(
                select(PostModel.user_id, PostModel.project_id).where(PostModel.id == post_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return SemanticMemoryScope(
            user_id=row.user_id,
            level=SemanticMemoryScopeLevel.PROJECT,
            project_id=row.project_id,
        )


__all__ = ["SQLAlchemyPostMemoryScopeResolver"]
