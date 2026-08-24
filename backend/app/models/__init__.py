"""SQLAlchemy model registry used by Alembic metadata discovery."""

from app.models.conversations import ConversationModel, MessageModel

__all__ = ["ConversationModel", "MessageModel"]
