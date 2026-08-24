from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.models.posts import PostGenerationModel, PostModel
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import GenerationStatus, PostWorkflowSection
from app.modules.posts.domain.state import PostGenerationState
from app.modules.posts.orchestration import SupervisorCheckpoint


class SQLAlchemySupervisorCheckpointStore:
    """Transactional adapter for durable Post Supervisor checkpoints."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load(self, *, generation_id: UUID) -> SupervisorCheckpoint | None:
        async with self._session_factory() as session:
            context = await _generation_context(session, generation_id=generation_id)
            if context is None:
                return None
            generation, post = context
            state = await SQLAlchemyPostRepository(session).get_workflow_state(
                generation_id=generation_id,
                post_id=post.id,
                scope=_scope(post),
            )
            if state is None:
                return None
            return SupervisorCheckpoint(
                generation_id=generation_id,
                post_id=post.id,
                status=GenerationStatus(generation.status),
                state=state,
            )

    async def write_section(
        self,
        *,
        generation_id: UUID,
        section: PostWorkflowSection,
        value: Any,
        expected_version: int,
    ) -> PostGenerationState | None:
        async with self._session_factory.begin() as session:
            context = await _generation_context(session, generation_id=generation_id)
            if context is None:
                return None
            _, post = context
            return await SQLAlchemyPostRepository(session).update_workflow_state(
                generation_id=generation_id,
                post_id=post.id,
                scope=_scope(post),
                section=section,
                value=value,
                expected_version=expected_version,
            )

    async def set_generation_status(
        self,
        *,
        generation_id: UUID,
        status: GenerationStatus,
    ) -> bool:
        async with self._session_factory.begin() as session:
            generation = await session.get(PostGenerationModel, generation_id)
            if generation is None:
                return False
            generation.status = status.value
            await session.flush()
            return True


async def _generation_context(
    session: AsyncSession,
    *,
    generation_id: UUID,
) -> tuple[PostGenerationModel, PostModel] | None:
    statement = (
        select(PostGenerationModel, PostModel)
        .join(PostModel, PostModel.id == PostGenerationModel.post_id)
        .where(PostGenerationModel.id == generation_id)
    )
    row = (await session.execute(statement)).one_or_none()
    return (row[0], row[1]) if row else None


def _scope(post: PostModel) -> PostScope:
    return PostScope(user_id=post.user_id, project_id=post.project_id)


__all__ = ["SQLAlchemySupervisorCheckpointStore"]
