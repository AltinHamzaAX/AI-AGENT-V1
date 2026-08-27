"""Conversational layer for Posts: intent classification and reply composition.

The chat boundary talks to the provider directly instead of through the
generation AgentRuntime: a chat turn is interactive, has no generation to trace
against, and must answer in one round trip.
"""

from app.modules.posts.chat.responder import (
    BASE_PERSONA,
    ConversationResponder,
    fallback_reply,
)
from app.modules.posts.chat.router import ChatExchange, ConversationRouter
from app.modules.posts.chat.schemas import ConversationRouterOutput

__all__ = [
    "BASE_PERSONA",
    "ChatExchange",
    "ConversationResponder",
    "ConversationRouter",
    "ConversationRouterOutput",
    "fallback_reply",
]
