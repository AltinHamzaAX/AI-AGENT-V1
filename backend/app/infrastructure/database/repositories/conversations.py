from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversations import ConversationModel, MessageModel
from app.shared.conversations.domain import (
    Conversation,
    ConversationScope,
    Message,
    MessagePage,
    MessageRole,
)


def _conversation(model: ConversationModel) -> Conversation:
    return Conversation(
        id=model.id,
        scope=ConversationScope(user_id=model.user_id, project_id=model.project_id),
        title=model.title,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _message(model: MessageModel) -> Message:
    return Message(
        id=model.id,
        conversation_id=model.conversation_id,
        sequence=model.sequence,
        role=MessageRole(model.role),
        content=model.content,
        metadata=dict(model.message_metadata),
        created_at=model.created_at,
    )


class SQLAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        scope: ConversationScope,
        title: str | None,
    ) -> Conversation:
        model = ConversationModel(
            user_id=scope.user_id,
            project_id=scope.project_id,
            title=title,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _conversation(model)

    async def get(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Conversation | None:
        model = await self._find_conversation(
            conversation_id=conversation_id,
            scope=scope,
        )
        return _conversation(model) if model else None

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any],
    ) -> Message | None:
        conversation = await self._find_conversation(
            conversation_id=conversation_id,
            scope=scope,
            for_update=True,
        )
        if conversation is None:
            return None

        sequence_statement = select(func.coalesce(func.max(MessageModel.sequence), 0) + 1).where(
            MessageModel.conversation_id == conversation_id
        )
        sequence = int((await self._session.execute(sequence_statement)).scalar_one())
        model = MessageModel(
            conversation_id=conversation_id,
            sequence=sequence,
            role=role.value,
            content=content,
            message_metadata=metadata,
        )
        conversation.updated_at = datetime.now(UTC)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _message(model)

    async def history(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        offset: int,
        limit: int,
    ) -> MessagePage | None:
        if await self._find_conversation(conversation_id=conversation_id, scope=scope) is None:
            return None

        filter_by_conversation = MessageModel.conversation_id == conversation_id
        total_statement = select(func.count(MessageModel.id)).where(filter_by_conversation)
        total = int((await self._session.execute(total_statement)).scalar_one())
        history_statement = (
            select(MessageModel)
            .where(filter_by_conversation)
            .order_by(MessageModel.sequence.asc())
            .offset(offset)
            .limit(limit)
        )
        models = (await self._session.execute(history_statement)).scalars().all()
        return MessagePage(
            items=tuple(_message(model) for model in models),
            total=total,
            offset=offset,
            limit=limit,
        )

    async def _find_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        for_update: bool = False,
    ) -> ConversationModel | None:
        statement = select(ConversationModel).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == scope.user_id,
            ConversationModel.project_id == scope.project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()
