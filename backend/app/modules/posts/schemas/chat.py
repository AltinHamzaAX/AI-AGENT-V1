from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, model_validator

from app.modules.posts.domain.chat import (
    ChatAction,
    ChatIntent,
    ContextAsset,
    ConversationContext,
    GeneratedPostRef,
)
from app.modules.posts.domain.enums import UnderstandingField
from app.modules.posts.schemas.models import GenerationArtifactRead, PostGenerationRead
from app.modules.posts.services.chat import (
    ChatConversationState,
    ChatWorkflowStart,
    PostChatTurn,
)
from app.shared.assets.domain import AssetRole
from app.shared.conversations.schemas import MessageRead


class ChatTurnCreate(BaseModel):
    """One client turn: new text, or a message already stored with its uploads."""

    content: str | None = Field(default=None, max_length=50_000)
    message_id: UUID | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.content is None) == (self.message_id is None):
            raise ValueError("provide either content or message_id, not both")
        if self.content is not None and not self.content.strip():
            raise ValueError("message content cannot be blank")
        return self


class ContextAssetRead(BaseModel):
    id: UUID
    message_id: UUID
    role: AssetRole
    original_filename: str
    mime_type: str
    width: int | None
    height: int | None

    @classmethod
    def from_domain(cls, asset: ContextAsset) -> Self:
        return cls(
            id=asset.id,
            message_id=asset.message_id,
            role=asset.role,
            original_filename=asset.original_filename,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
        )


class GeneratedPostRead(BaseModel):
    post_id: UUID
    generation_id: UUID
    attempt: int
    revises_generation_id: UUID | None
    instruction: str | None

    @classmethod
    def from_domain(cls, reference: GeneratedPostRef) -> Self:
        return cls(
            post_id=reference.post_id,
            generation_id=reference.generation_id,
            attempt=reference.attempt,
            revises_generation_id=reference.revises_generation_id,
            instruction=reference.instruction,
        )


class ConversationContextRead(BaseModel):
    """What the assistant already knows, as the client should see it."""

    business: str | None
    brand: str | None
    product_service: str | None
    goal: str | None
    audience: str | None
    market: str | None
    location: str | None
    platform: str | None
    language: str | None
    offer: str | None
    cta_intent: str | None
    style_preferences: list[str]
    constraints: list[str]
    attachments: list[ContextAssetRead]
    missing_fields: list[UnderstandingField]
    generated_posts: list[GeneratedPostRead]
    revision_instructions: list[str]

    @classmethod
    def from_domain(cls, context: ConversationContext) -> Self:
        return cls(
            **{field.value: getattr(context, field.value) for field in UnderstandingField},
            style_preferences=list(context.style_preferences),
            constraints=list(context.constraints),
            attachments=[ContextAssetRead.from_domain(asset) for asset in context.assets],
            missing_fields=context.missing_fields,
            generated_posts=[
                GeneratedPostRead.from_domain(item) for item in context.generated_posts
            ],
            revision_instructions=list(context.revision_instructions),
        )


class ChatWorkflowRead(BaseModel):
    post_id: UUID
    generation_id: UUID
    attempt: int
    deduplicated: bool
    revises_generation_id: UUID | None

    @classmethod
    def from_domain(cls, workflow: ChatWorkflowStart) -> Self:
        return cls(
            post_id=workflow.post_id,
            generation_id=workflow.generation_id,
            attempt=workflow.attempt,
            deduplicated=workflow.deduplicated,
            revises_generation_id=workflow.revises_generation_id,
        )


class ChatTurnRead(BaseModel):
    """The stored pair of messages plus what the turn decided and started."""

    user: MessageRead
    assistant: MessageRead
    intent: ChatIntent
    action: ChatAction
    questions: list[str]
    workflow: ChatWorkflowRead | None
    context: ConversationContextRead

    @classmethod
    def from_domain(cls, turn: PostChatTurn) -> Self:
        return cls(
            user=MessageRead.from_domain(turn.user),
            assistant=MessageRead.from_domain(turn.assistant),
            intent=turn.intent,
            action=turn.action,
            questions=list(turn.questions),
            workflow=(
                ChatWorkflowRead.from_domain(turn.workflow) if turn.workflow is not None else None
            ),
            context=ConversationContextRead.from_domain(turn.context),
        )


class ChatStateRead(BaseModel):
    """Everything needed to restore a Posts chat after a reload."""

    context: ConversationContextRead
    post_id: UUID | None
    generation: PostGenerationRead | None
    artifacts: list[GenerationArtifactRead]

    @classmethod
    def from_domain(cls, state: ChatConversationState) -> Self:
        return cls(
            context=ConversationContextRead.from_domain(state.context),
            post_id=state.post_id,
            generation=(
                PostGenerationRead.from_domain(state.generation)
                if state.generation is not None
                else None
            ),
            artifacts=[
                GenerationArtifactRead.from_domain(artifact) for artifact in state.artifacts
            ],
        )


__all__ = [
    "ChatStateRead",
    "ChatTurnCreate",
    "ChatTurnRead",
    "ChatWorkflowRead",
    "ContextAssetRead",
    "ConversationContextRead",
    "GeneratedPostRead",
]
