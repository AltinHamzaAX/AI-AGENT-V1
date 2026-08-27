from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posts import PostConversationContextModel
from app.modules.posts.domain.chat import ConversationContext
from app.modules.posts.domain.entities import PostScope


class SQLAlchemyPostConversationContextRepository:
    """One accumulated chat context per conversation, scoped to its owner."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
    ) -> ConversationContext | None:
        model = await self._find(conversation_id=conversation_id, scope=scope)
        if model is None:
            return None
        return ConversationContext.model_validate(model.data)

    async def save(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
        context: ConversationContext,
    ) -> ConversationContext:
        data = context.model_dump(mode="json")
        model = await self._find(conversation_id=conversation_id, scope=scope, for_update=True)
        if model is None:
            model = PostConversationContextModel(
                conversation_id=conversation_id,
                user_id=scope.user_id,
                project_id=scope.project_id,
                version=1,
                data=data,
            )
            self._session.add(model)
        else:
            model.version += 1
            model.data = data
        await self._session.flush()
        await self._session.refresh(model)
        return ConversationContext.model_validate(model.data)

    async def _find(
        self,
        *,
        conversation_id: UUID,
        scope: PostScope,
        for_update: bool = False,
    ) -> PostConversationContextModel | None:
        statement = select(PostConversationContextModel).where(
            PostConversationContextModel.conversation_id == conversation_id,
            PostConversationContextModel.user_id == scope.user_id,
            PostConversationContextModel.project_id == scope.project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()


__all__ = ["SQLAlchemyPostConversationContextRepository"]
