from typing import Any
from uuid import UUID

from app.shared.conversations.contracts import ConversationRepository
from app.shared.conversations.domain import (
    Conversation,
    ConversationNotFoundError,
    ConversationScope,
    Message,
    MessagePage,
    MessageRole,
)


class ConversationService:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        scope: ConversationScope,
        title: str | None = None,
    ) -> Conversation:
        return await self._repository.create(scope=scope, title=title)

    async def get(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Conversation:
        conversation = await self._repository.get(
            conversation_id=conversation_id,
            scope=scope,
        )
        if conversation is None:
            raise ConversationNotFoundError
        return conversation

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        message = await self._repository.append_message(
            conversation_id=conversation_id,
            scope=scope,
            role=role,
            content=content,
            metadata=metadata or {},
        )
        if message is None:
            raise ConversationNotFoundError
        return message

    async def history(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        offset: int,
        limit: int,
    ) -> MessagePage:
        page = await self._repository.history(
            conversation_id=conversation_id,
            scope=scope,
            offset=offset,
            limit=limit,
        )
        if page is None:
            raise ConversationNotFoundError
        return page
