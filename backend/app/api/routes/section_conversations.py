from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies.conversations import (
    ConversationScopeDependency,
    ConversationServiceDependency,
)
from app.dependencies.posts import PostChatServiceDependency
from app.modules.posts.domain.exceptions import ChatMessageNotFoundError
from app.modules.posts.providers import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from app.modules.posts.schemas import (
    ChatStateRead,
    ChatTurnCreate,
    ChatTurnRead,
    ChatWorkflowRead,
)
from app.shared.conversations.domain import ConversationKind, ConversationNotFoundError
from app.shared.conversations.schemas import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessagePageRead,
    MessageRead,
)


def section_conversation_router(kind: ConversationKind) -> APIRouter:
    router = APIRouter()

    async def required_conversation(
        conversation_id: UUID,
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
    ):
        try:
            conversation = await service.get(conversation_id=conversation_id, scope=scope)
        except ConversationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        if conversation.kind is not kind:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    @router.get("/conversations", response_model=list[ConversationRead])
    async def list_conversations(
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> list[ConversationRead]:
        items = await service.list(scope=scope, kind=kind, offset=offset, limit=limit)
        return [ConversationRead.from_domain(item) for item in items]

    @router.post(
        "/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED
    )
    async def create_conversation(
        payload: ConversationCreate,
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
    ) -> ConversationRead:
        conversation = await service.create(scope=scope, title=payload.title, kind=kind)
        return ConversationRead.from_domain(conversation)

    @router.get("/conversations/{conversation_id}", response_model=ConversationRead)
    async def get_conversation(
        conversation_id: UUID,
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
    ) -> ConversationRead:
        return ConversationRead.from_domain(
            await required_conversation(conversation_id, scope, service)
        )

    @router.get(
        "/conversations/{conversation_id}/messages", response_model=MessagePageRead
    )
    async def history(
        conversation_id: UUID,
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> MessagePageRead:
        await required_conversation(conversation_id, scope, service)
        page = await service.history(
            conversation_id=conversation_id, scope=scope, offset=offset, limit=limit
        )
        return MessagePageRead.from_domain(page)

    @router.post(
        "/conversations/{conversation_id}/messages",
        response_model=MessageRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def append_message(
        conversation_id: UUID,
        payload: MessageCreate,
        scope: ConversationScopeDependency,
        service: ConversationServiceDependency,
    ) -> MessageRead:
        await required_conversation(conversation_id, scope, service)
        message = await service.append_message(
            conversation_id=conversation_id,
            scope=scope,
            role=payload.role,
            content=payload.content,
            metadata=payload.metadata,
        )
        return MessageRead.from_domain(message)

    return router


posts_conversations_router = section_conversation_router(ConversationKind.POST)
campaigns_conversations_router = section_conversation_router(ConversationKind.CAMPAIGN)


def _assistant_unavailable(exc: ProviderError) -> HTTPException:
    if isinstance(exc, ProviderRateLimitError | ProviderQuotaError):
        return HTTPException(
            status_code=429,
            detail="The assistant is rate limited; try again shortly",
        )
    return HTTPException(
        status_code=502,
        detail="The assistant is temporarily unavailable; the turn was not saved",
    )


@posts_conversations_router.post(
    "/conversations/{conversation_id}/turns",
    response_model=ChatTurnRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_chat_turn(
    conversation_id: UUID,
    payload: ChatTurnCreate,
    scope: ConversationScopeDependency,
    service: PostChatServiceDependency,
) -> ChatTurnRead:
    """Route one client message and answer it.

    The turn decides for itself whether it only replies, asks for what is
    missing, or starts the Post workflow; the whole decision and its effects
    commit together or not at all.
    """
    try:
        turn = await service.reply(
            conversation_id=conversation_id,
            scope=scope,
            content=payload.content,
            message_id=payload.message_id,
            metadata=payload.metadata,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except ChatMessageNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Message not found, or it is not the latest client message",
        ) from exc
    except ProviderError as exc:
        raise _assistant_unavailable(exc) from exc
    return ChatTurnRead.from_domain(turn)


@posts_conversations_router.post(
    "/conversations/{conversation_id}/generations",
    response_model=ChatWorkflowRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation_generation(
    conversation_id: UUID,
    scope: ConversationScopeDependency,
    service: PostChatServiceDependency,
) -> ChatWorkflowRead:
    """Start the Post workflow on an explicit client command.

    Same effect as the routed `GENERATE_POST` intent without classifying a
    message: repeating the command while nothing new was said returns the
    generation already running.
    """
    try:
        workflow = await service.start_generation(conversation_id=conversation_id, scope=scope)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return ChatWorkflowRead.from_domain(workflow)


@posts_conversations_router.get(
    "/conversations/{conversation_id}/state",
    response_model=ChatStateRead,
)
async def get_conversation_chat_state(
    conversation_id: UUID,
    scope: ConversationScopeDependency,
    service: PostChatServiceDependency,
) -> ChatStateRead:
    """The accumulated context and the latest generation, for a reopened chat."""
    try:
        state = await service.state(conversation_id=conversation_id, scope=scope)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return ChatStateRead.from_domain(state)
