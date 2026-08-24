"""Conversation history shared by Posts and Campaigns."""

from app.shared.conversations.domain import (
    Conversation,
    ConversationScope,
    Message,
    MessagePage,
    MessageRole,
)

__all__ = [
    "Conversation",
    "ConversationScope",
    "Message",
    "MessagePage",
    "MessageRole",
]
