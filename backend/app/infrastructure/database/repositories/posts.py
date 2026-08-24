from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversations import ConversationModel
from app.models.posts import GenerationArtifactModel, PostGenerationModel, PostModel
from app.modules.posts.domain.entities import (
    GenerationArtifact,
    Post,
    PostGeneration,
    PostScope,
)
from app.modules.posts.domain.enums import GenerationArtifactKind, GenerationStatus


def _post(model: PostModel) -> Post:
    return Post(
        id=model.id,
        scope=PostScope(user_id=model.user_id, project_id=model.project_id),
        conversation_id=model.conversation_id,
        campaign_id=model.campaign_id,
        title=model.title,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _generation(model: PostGenerationModel) -> PostGeneration:
    return PostGeneration(
        id=model.id,
        post_id=model.post_id,
        attempt=model.attempt,
        status=GenerationStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _artifact(model: GenerationArtifactModel) -> GenerationArtifact:
    return GenerationArtifact(
        id=model.id,
        generation_id=model.generation_id,
        kind=GenerationArtifactKind(model.kind),
        storage_key=model.storage_key,
        mime_type=model.mime_type,
        size_bytes=model.size_bytes,
        checksum=model.checksum,
        width=model.width,
        height=model.height,
        metadata=dict(model.artifact_metadata),
        created_at=model.created_at,
    )


class SQLAlchemyPostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def conversation_exists(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
    ) -> bool:
        statement = select(ConversationModel.id).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == scope.user_id,
            ConversationModel.project_id == scope.project_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def create_post(
        self,
        *,
        scope: PostScope,
        conversation_id: UUID | None,
        campaign_id: UUID | None,
        title: str | None,
    ) -> Post:
        model = PostModel(
            user_id=scope.user_id,
            project_id=scope.project_id,
            conversation_id=conversation_id,
            campaign_id=campaign_id,
            title=title,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _post(model)

    async def get_post(self, *, post_id: UUID, scope: PostScope) -> Post | None:
        model = await self._find_post(post_id=post_id, scope=scope)
        return _post(model) if model else None

    async def create_generation(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGeneration | None:
        post = await self._find_post(post_id=post_id, scope=scope, for_update=True)
        if post is None:
            return None
        attempt_statement = select(
            func.coalesce(func.max(PostGenerationModel.attempt), 0) + 1
        ).where(PostGenerationModel.post_id == post_id)
        attempt = int((await self._session.execute(attempt_statement)).scalar_one())
        model = PostGenerationModel(
            post_id=post_id,
            attempt=attempt,
            status=GenerationStatus.PENDING.value,
        )
        post.updated_at = datetime.now(UTC)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _generation(model)

    async def get_generation(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> PostGeneration | None:
        model = await self._find_generation(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
        return _generation(model) if model else None

    async def list_generations(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[PostGeneration] | None:
        if await self._find_post(post_id=post_id, scope=scope) is None:
            return None
        statement = (
            select(PostGenerationModel)
            .where(PostGenerationModel.post_id == post_id)
            .order_by(PostGenerationModel.attempt)
        )
        models = (await self._session.execute(statement)).scalars().all()
        return tuple(_generation(model) for model in models)

    async def update_generation_status(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        status: GenerationStatus,
    ) -> PostGeneration | None:
        model = await self._find_generation(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            for_update=True,
        )
        if model is None:
            return None
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(model)
        return _generation(model)

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
    ) -> GenerationArtifact | None:
        if (
            await self._find_generation(
                generation_id=generation_id,
                post_id=post_id,
                scope=scope,
            )
            is None
        ):
            return None
        model = GenerationArtifactModel(
            id=artifact_id,
            generation_id=generation_id,
            kind=kind.value,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum=checksum,
            width=width,
            height=height,
            artifact_metadata=metadata,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _artifact(model)

    async def list_artifacts(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
    ) -> Sequence[GenerationArtifact] | None:
        if (
            await self._find_generation(
                generation_id=generation_id,
                post_id=post_id,
                scope=scope,
            )
            is None
        ):
            return None
        statement = (
            select(GenerationArtifactModel)
            .where(GenerationArtifactModel.generation_id == generation_id)
            .order_by(GenerationArtifactModel.created_at, GenerationArtifactModel.id)
        )
        models = (await self._session.execute(statement)).scalars().all()
        return tuple(_artifact(model) for model in models)

    async def _find_post(
        self,
        *,
        post_id: UUID,
        scope: PostScope,
        for_update: bool = False,
    ) -> PostModel | None:
        statement = select(PostModel).where(
            PostModel.id == post_id,
            PostModel.user_id == scope.user_id,
            PostModel.project_id == scope.project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def _find_generation(
        self,
        *,
        generation_id: UUID,
        post_id: UUID,
        scope: PostScope,
        for_update: bool = False,
    ) -> PostGenerationModel | None:
        statement = (
            select(PostGenerationModel)
            .join(PostModel, PostModel.id == PostGenerationModel.post_id)
            .where(
                PostGenerationModel.id == generation_id,
                PostGenerationModel.post_id == post_id,
                PostModel.user_id == scope.user_id,
                PostModel.project_id == scope.project_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()
