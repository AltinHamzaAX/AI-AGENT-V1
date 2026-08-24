from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.shared.conversations.domain import ConversationScope
from app.shared.conversations.service import ConversationService


def get_conversation_scope(
    user_id: Annotated[UUID, Header(alias="X-User-ID")],
    project_id: Annotated[UUID, Header(alias="X-Project-ID")],
) -> ConversationScope:
    """Build the data scope from identity values supplied by the API boundary."""
    return ConversationScope(user_id=user_id, project_id=project_id)


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> ConversationService:
    repository = SQLAlchemyConversationRepository(session)
    return ConversationService(repository)


ConversationScopeDependency = Annotated[ConversationScope, Depends(get_conversation_scope)]
ConversationServiceDependency = Annotated[ConversationService, Depends(get_conversation_service)]
