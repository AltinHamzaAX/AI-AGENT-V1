"""The Posts conversational assistant.

Every client message runs the same pipeline: understand the intent, fold the
new facts into the conversation's memory, decide the one action the turn may
perform, perform it, and answer. Generation and revision are reachable only
through their own intents, so an ordinary message never starts the workflow.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.posts.chat.responder import ConversationResponder, fallback_reply
from app.modules.posts.chat.router import ChatExchange, ConversationRouter
from app.modules.posts.domain.chat import (
    ChatAction,
    ChatIntent,
    ChatIntentRouter,
    ContextAsset,
    ContextUpdate,
    ConversationContext,
    GeneratedPostRef,
    RoutedTurn,
    detects_albanian,
    explicit_generation_request,
    extract_cta_intent,
    extract_goal,
    inferable_goal,
    utcnow,
)
from app.modules.posts.domain.entities import GenerationArtifact, PostGeneration, PostScope
from app.modules.posts.domain.enums import PostWorkflowSection, UnderstandingField
from app.modules.posts.domain.exceptions import ChatMessageNotFoundError
from app.modules.posts.repositories.contracts import PostConversationContextRepository
from app.modules.posts.services.posts import PostsService
from app.shared.assets.domain import Asset
from app.shared.conversations.domain import (
    ConversationKind,
    ConversationNotFoundError,
    ConversationScope,
    Message,
    MessageRole,
)
from app.shared.conversations.service import ConversationService

#: Turns handed to the generation workflow as the client brief. Older turns are
#: already represented by the accumulated project context.
WORKFLOW_HISTORY_TURNS = 60
#: The second explicit request proceeds on an inferred goal rather than asking
#: the same question forever.
GOAL_INFERENCE_THRESHOLD = 2
#: Languages this boundary can confirm from the client's own text. Anything
#: else the model names is dropped rather than carried into the contract.
VERIFIABLE_LANGUAGES = frozenset({"english"})


class ConversationAssetReader(Protocol):
    async def list_for_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Sequence[Asset]: ...


@dataclass(frozen=True, slots=True)
class ChatWorkflowStart:
    post_id: UUID
    generation_id: UUID
    attempt: int
    deduplicated: bool
    revises_generation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PostChatTurn:
    user: Message
    assistant: Message
    intent: ChatIntent
    action: ChatAction
    context: ConversationContext
    questions: tuple[str, ...] = ()
    workflow: ChatWorkflowStart | None = None
    generation_ready: bool = False


@dataclass(frozen=True, slots=True)
class ChatConversationState:
    context: ConversationContext
    post_id: UUID | None = None
    generation: PostGeneration | None = None
    artifacts: tuple[GenerationArtifact, ...] = ()


class PostChatService:
    def __init__(
        self,
        *,
        conversations: ConversationService,
        posts: PostsService,
        contexts: PostConversationContextRepository,
        assets: ConversationAssetReader,
        router: ConversationRouter,
        responder: ConversationResponder,
        intent_router: ChatIntentRouter | None = None,
    ) -> None:
        self._conversations = conversations
        self._posts = posts
        self._contexts = contexts
        self._assets = assets
        self._router = router
        self._responder = responder
        self._intents = intent_router or ChatIntentRouter()

    async def reply(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        content: str | None = None,
        message_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PostChatTurn:
        """Answer one client turn and perform whatever its intent allows.

        The whole turn runs inside the caller's transaction, so a failure after
        a generation was requested leaves neither the message nor the queued
        work behind.
        """
        await self._require_post_conversation(conversation_id=conversation_id, scope=scope)
        history = await self._history(conversation_id=conversation_id, scope=scope)
        user_message = await self._resolve_user_message(
            conversation_id=conversation_id,
            scope=scope,
            content=content,
            message_id=message_id,
            metadata=metadata or {},
            history=history,
        )
        exchanges = tuple(
            ChatExchange(role=message.role.value, content=message.content)
            for message in history
            if message.id != user_message.id
            and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
        )
        attachments = await self._attachments(conversation_id=conversation_id, scope=scope)
        stored = await self._contexts.get(
            conversation_id=conversation_id,
            scope=_post_scope(scope),
        )
        context = (stored or ConversationContext()).with_assets(attachments)
        new_attachments = [
            asset for asset in attachments if asset.message_id == user_message.id
        ]

        classified = await self._router.classify(
            message=user_message.content,
            history=exchanges,
            context=context,
            attachments=new_attachments,
        )
        client_texts = [
            user_message.content,
            *(item.content for item in exchanges if item.role == "user"),
        ]
        context = context.merge(classified.context_updates).with_request(user_message.content)
        context = _recover_stated_facts(context, message=user_message.content)
        context = _verify_language(context, client_texts=client_texts)
        if explicit_generation_request(user_message.content):
            context = context.with_generation_request()

        routed = self._intents.route(
            proposed=classified.intent,
            message=user_message.content,
            context=context,
        )
        routed, context = self._resolve_deadlock(routed, context=context)

        workflow: ChatWorkflowStart | None = None
        if routed.action in {ChatAction.GENERATE, ChatAction.REVISE}:
            revise = routed.action is ChatAction.REVISE
            if revise:
                context = context.with_revision_instructions(
                    classified.revision_instructions or [user_message.content]
                )
            workflow = await self._start_workflow(
                conversation_id=conversation_id,
                scope=scope,
                context=context,
                history=(*exchanges, ChatExchange(role="user", content=user_message.content)),
                latest_message=user_message.content,
                attachments=attachments,
                idempotency_key=f"chat-turn:{user_message.id}",
                revise=revise,
            )
            context = context.with_generated_post(
                GeneratedPostRef(
                    post_id=workflow.post_id,
                    generation_id=workflow.generation_id,
                    attempt=workflow.attempt,
                    requested_at=utcnow(),
                    revises_generation_id=workflow.revises_generation_id,
                    instruction=user_message.content if revise else None,
                )
            )

        answer, provider_metadata = await self._compose(
            routed=routed,
            message=user_message.content,
            history=exchanges,
            context=context,
            attachments=new_attachments,
            revision_instructions=classified.revision_instructions,
            workflow=workflow,
        )
        assistant_message = await self._conversations.append_message(
            conversation_id=conversation_id,
            scope=scope,
            role=MessageRole.ASSISTANT,
            content=answer,
            metadata={
                **provider_metadata,
                "chat": _turn_metadata(
                    routed,
                    classified.reason,
                    workflow,
                    generation_ready=context.generation_ready,
                ),
            },
        )
        saved = await self._contexts.save(
            conversation_id=conversation_id,
            scope=_post_scope(scope),
            context=context,
        )
        return PostChatTurn(
            user=user_message,
            assistant=assistant_message,
            intent=routed.intent,
            action=routed.action,
            context=saved,
            questions=tuple(routed.questions),
            workflow=workflow,
            generation_ready=saved.generation_ready,
        )

    async def start_generation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> ChatWorkflowStart:
        """Start generation on the client's explicit command, without a message.

        Same path as the routed GENERATE_POST intent, minus the classification:
        the request is already unambiguous.
        """
        await self._require_post_conversation(conversation_id=conversation_id, scope=scope)
        history = await self._history(conversation_id=conversation_id, scope=scope)
        attachments = await self._attachments(conversation_id=conversation_id, scope=scope)
        stored = await self._contexts.get(
            conversation_id=conversation_id,
            scope=_post_scope(scope),
        )
        context = (stored or ConversationContext()).with_assets(attachments)
        exchanges = tuple(
            ChatExchange(role=message.role.value, content=message.content)
            for message in history
            if message.role in {MessageRole.USER, MessageRole.ASSISTANT}
        )
        latest_client_message = next(
            (message.content for message in reversed(history) if message.role is MessageRole.USER),
            "",
        )
        revise = context.latest_generation is not None
        workflow = await self._start_workflow(
            conversation_id=conversation_id,
            scope=scope,
            context=context,
            history=exchanges,
            latest_message=latest_client_message,
            attachments=attachments,
            idempotency_key=f"chat-command:{conversation_id}:{len(history)}",
            revise=revise,
        )
        if not workflow.deduplicated:
            await self._contexts.save(
                conversation_id=conversation_id,
                scope=_post_scope(scope),
                context=context.with_generated_post(
                    GeneratedPostRef(
                        post_id=workflow.post_id,
                        generation_id=workflow.generation_id,
                        attempt=workflow.attempt,
                        requested_at=utcnow(),
                        revises_generation_id=workflow.revises_generation_id,
                    )
                ),
            )
        return workflow

    async def state(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> ChatConversationState:
        """What the client should see when a Posts chat is reopened."""
        await self._require_post_conversation(conversation_id=conversation_id, scope=scope)
        post_scope = _post_scope(scope)
        stored = await self._contexts.get(conversation_id=conversation_id, scope=post_scope)
        context = stored or ConversationContext()
        post = await self._posts.find_post_by_conversation(
            conversation_id=conversation_id,
            scope=post_scope,
        )
        if post is None:
            return ChatConversationState(context=context)
        generations = await self._posts.list_generations(post_id=post.id, scope=post_scope)
        latest = generations[-1] if generations else None
        if latest is None:
            return ChatConversationState(context=context, post_id=post.id)
        artifacts = await self._posts.list_artifacts(
            generation_id=latest.id,
            post_id=post.id,
            scope=post_scope,
        )
        return ChatConversationState(
            context=context,
            post_id=post.id,
            generation=latest,
            artifacts=tuple(artifacts),
        )

    async def _require_post_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> None:
        conversation = await self._conversations.get(
            conversation_id=conversation_id,
            scope=scope,
        )
        if conversation.kind is not ConversationKind.POST:
            raise ConversationNotFoundError

    async def _history(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> tuple[Message, ...]:
        page = await self._conversations.history(
            conversation_id=conversation_id,
            scope=scope,
            offset=0,
            limit=100,
        )
        return page.items

    async def _attachments(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> list[ContextAsset]:
        assets = await self._assets.list_for_conversation(
            conversation_id=conversation_id,
            scope=scope,
        )
        return [
            ContextAsset(
                id=asset.id,
                message_id=asset.message_id,
                role=asset.role,
                original_filename=asset.original_filename,
                mime_type=asset.mime_type,
                width=asset.width,
                height=asset.height,
            )
            for asset in assets
        ]

    async def _resolve_user_message(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        content: str | None,
        message_id: UUID | None,
        metadata: dict[str, Any],
        history: tuple[Message, ...],
    ) -> Message:
        if message_id is not None:
            target = next((item for item in history if item.id == message_id), None)
            if target is None or target.role is not MessageRole.USER:
                raise ChatMessageNotFoundError
            if any(item.sequence > target.sequence for item in history):
                raise ChatMessageNotFoundError
            return target
        if content is None:
            raise ChatMessageNotFoundError
        return await self._conversations.append_message(
            conversation_id=conversation_id,
            scope=scope,
            role=MessageRole.USER,
            content=content,
            metadata=metadata,
        )

    def _resolve_deadlock(
        self,
        routed: RoutedTurn,
        *,
        context: ConversationContext,
    ) -> tuple[RoutedTurn, ConversationContext]:
        """Let a repeated, explicit order through on an inferred goal.

        The client has now asked twice in plain words and the promoted subject
        is known; refusing again would only loop the same question.
        """
        if routed.action is not ChatAction.ASK:
            return routed, context
        if context.explicit_generation_requests < GOAL_INFERENCE_THRESHOLD:
            return routed, context
        goal = inferable_goal(context)
        if goal is None or context.goal is not None:
            return routed, context
        updated = context.with_inferred(UnderstandingField.GOAL, goal)
        plan = updated.clarification()
        if plan.requires_user_input:
            return routed, context
        return (
            RoutedTurn(
                intent=ChatIntent.GENERATE_POST,
                action=ChatAction.GENERATE,
                reason="The client repeated the request; the goal follows from the subject.",
            ),
            updated,
        )

    async def _start_workflow(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        context: ConversationContext,
        history: Sequence[ChatExchange],
        latest_message: str,
        attachments: Sequence[ContextAsset],
        idempotency_key: str,
        revise: bool,
    ) -> ChatWorkflowStart:
        post_scope = _post_scope(scope)
        post = await self._posts.find_post_by_conversation(
            conversation_id=conversation_id,
            scope=post_scope,
        )
        if post is None:
            post = await self._posts.create_post(
                scope=post_scope,
                conversation_id=conversation_id,
                campaign_id=None,
                title=context.product_service or context.business or context.brand,
            )
        previous = context.latest_generation
        generation = await self._posts.request_generation(
            post_id=post.id,
            scope=post_scope,
            idempotency_key=idempotency_key,
        )
        if not generation.deduplicated:
            await self._posts.write_workflow_section(
                generation_id=generation.id,
                post_id=post.id,
                scope=post_scope,
                section=PostWorkflowSection.CONVERSATION_CONTEXT,
                value=_workflow_conversation_context(
                    context=context,
                    history=history,
                    latest_message=latest_message,
                    attachments=attachments,
                ),
                expected_version=1,
            )
        return ChatWorkflowStart(
            post_id=post.id,
            generation_id=generation.id,
            attempt=generation.attempt,
            deduplicated=generation.deduplicated,
            revises_generation_id=previous.generation_id if revise and previous else None,
        )

    async def _compose(
        self,
        *,
        routed: RoutedTurn,
        message: str,
        history: Sequence[ChatExchange],
        context: ConversationContext,
        attachments: Sequence[ContextAsset],
        revision_instructions: Sequence[str],
        workflow: ChatWorkflowStart | None,
    ) -> tuple[str, dict[str, Any]]:
        response = await self._responder.respond(
            intent=routed.intent,
            action=routed.action,
            message=message,
            history=history,
            context=context,
            attachments=attachments,
            questions=routed.questions,
            started_generation=workflow is not None,
            revision_instructions=revision_instructions,
        )
        metadata = {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        answer = response.text.strip()
        if answer:
            return answer, metadata
        fallback = fallback_reply(routed.intent, language=context.language)
        if fallback is None:
            raise ValueError("The chat model returned an empty response")
        return fallback, {**metadata, "reply_fallback": True}


def _recover_stated_facts(
    context: ConversationContext,
    *,
    message: str,
) -> ConversationContext:
    """Read off the client's own sentence what the model failed to extract.

    Asking again for something the client just said is the one failure that
    makes an assistant feel deaf, so the stated wording wins over the model's
    omission. Only the two facts a generation cannot start without are read this
    way, and only from a closed vocabulary, so nothing is invented.
    """
    update = ContextUpdate(
        goal=extract_goal(message) if context.goal is None else None,
        cta_intent=extract_cta_intent(message) if context.cta_intent is None else None,
    )
    return context.merge(update)


def _verify_language(
    context: ConversationContext,
    *,
    client_texts: Sequence[str],
) -> ConversationContext:
    """Decide the client's language from their own words, not from a claim.

    The stored language chooses the wording of clarification questions, so a
    model that guesses it wrong makes the assistant answer an English client in
    Albanian. What the client actually typed settles it.
    """
    if detects_albanian(client_texts):
        return context.merge(ContextUpdate(language="shqip"))
    if context.language in VERIFIABLE_LANGUAGES:
        return context
    # A language nothing can confirm is worse than none: the contract falls back
    # to its declared default rather than promising a post in a language the
    # client never wrote a word in.
    return context.model_copy(deep=True, update={"language": None})


def _turn_metadata(
    routed: RoutedTurn,
    reason: str,
    workflow: ChatWorkflowStart | None,
    *,
    generation_ready: bool,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "intent": routed.intent.value,
        "action": routed.action.value,
        "reason": reason or routed.reason,
        "generation_ready": generation_ready,
    }
    if routed.questions:
        metadata["questions"] = list(routed.questions)
    if workflow is not None:
        metadata["post_id"] = str(workflow.post_id)
        metadata["generation_id"] = str(workflow.generation_id)
        metadata["attempt"] = workflow.attempt
        if workflow.revises_generation_id is not None:
            metadata["revises_generation_id"] = str(workflow.revises_generation_id)
    return metadata


def _workflow_conversation_context(
    *,
    context: ConversationContext,
    history: Sequence[ChatExchange],
    latest_message: str,
    attachments: Sequence[ContextAsset],
) -> dict[str, Any]:
    """The brief the Supervisor's first stage reads, in its declared shape.

    The latest client message is carried in its own field, so it is not
    repeated as the last history turn: the extraction stage gives that field
    priority and a duplicate would only blur it.
    """
    turns = [
        {"role": exchange.role, "content": exchange.content}
        for exchange in history[-WORKFLOW_HISTORY_TURNS:]
        if exchange.role in {"user", "assistant"} and exchange.content.strip()
    ]
    if turns and turns[-1]["role"] == "user" and turns[-1]["content"] == latest_message.strip():
        turns.pop()
    project_context = context.project_context()
    if context.revision_instructions:
        project_context["revision_instructions"] = list(context.revision_instructions)
    return {
        "conversation_history": turns,
        "latest_message": latest_message,
        "attachments": [
            {
                "id": str(asset.id),
                "role": asset.role.value,
                "original_filename": asset.original_filename,
                "mime_type": asset.mime_type,
                "width": asset.width,
                "height": asset.height,
                "metadata": {},
            }
            for asset in attachments
        ],
        "project_context": project_context,
    }


def _post_scope(scope: ConversationScope) -> PostScope:
    return PostScope(user_id=scope.user_id, project_id=scope.project_id)


__all__ = [
    "GOAL_INFERENCE_THRESHOLD",
    "WORKFLOW_HISTORY_TURNS",
    "ChatConversationState",
    "ChatWorkflowStart",
    "ConversationAssetReader",
    "PostChatService",
    "PostChatTurn",
]
