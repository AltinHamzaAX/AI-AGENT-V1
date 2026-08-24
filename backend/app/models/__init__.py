"""SQLAlchemy model registry used by Alembic metadata discovery."""

from app.models.assets import AssetModel
from app.models.conversations import ConversationModel, MessageModel

__all__ = ["AssetModel", "ConversationModel", "MessageModel"]
