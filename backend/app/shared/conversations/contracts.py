from typing import Any, Protocol
from uuid import UUID

from app.shared.conversations.domain import (
    Conversation,
    ConversationScope,
    Message,
    MessagePage,
    MessageRole,
)


class ConversationRepository(Protocol):
    async def create(
        self,
        *,
        scope: ConversationScope,
        title: str | None,
    ) -> Conversation: ...

    async def get(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Conversation | None: ...

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any],
    ) -> Message | None: ...

    async def history(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        offset: int,
        limit: int,
    ) -> MessagePage | None: ...
