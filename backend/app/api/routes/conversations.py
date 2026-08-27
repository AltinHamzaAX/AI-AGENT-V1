from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies.conversations import (
    ConversationScopeDependency,
    ConversationServiceDependency,
)
from app.shared.conversations.domain import ConversationKind, ConversationNotFoundError
from app.shared.conversations.schemas import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessagePageRead,
    MessageRead,
)

router = APIRouter()


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Conversation not found",
    )


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    scope: ConversationScopeDependency,
    service: ConversationServiceDependency,
) -> ConversationRead:
    conversation = await service.create(scope=scope, title=payload.title, kind=payload.type)
    return ConversationRead.from_domain(conversation)


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    scope: ConversationScopeDependency,
    service: ConversationServiceDependency,
    type: ConversationKind,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[ConversationRead]:
    conversations = await service.list(scope=scope, kind=type, offset=offset, limit=limit)
    return [ConversationRead.from_domain(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: UUID,
    scope: ConversationScopeDependency,
    service: ConversationServiceDependency,
) -> ConversationRead:
    try:
        conversation = await service.get(conversation_id=conversation_id, scope=scope)
    except ConversationNotFoundError as exc:
        raise _not_found() from exc
    return ConversationRead.from_domain(conversation)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    conversation_id: UUID,
    payload: MessageCreate,
    scope: ConversationScopeDependency,
    service: ConversationServiceDependency,
) -> MessageRead:
    try:
        message = await service.append_message(
            conversation_id=conversation_id,
            scope=scope,
            role=payload.role,
            content=payload.content,
            metadata=payload.metadata,
        )
    except ConversationNotFoundError as exc:
        raise _not_found() from exc
    return MessageRead.from_domain(message)


@router.get("/{conversation_id}/messages", response_model=MessagePageRead)
async def get_history(
    conversation_id: UUID,
    scope: ConversationScopeDependency,
    service: ConversationServiceDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePageRead:
    try:
        page = await service.history(
            conversation_id=conversation_id,
            scope=scope,
            offset=offset,
            limit=limit,
        )
    except ConversationNotFoundError as exc:
        raise _not_found() from exc
    return MessagePageRead.from_domain(page)
