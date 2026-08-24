"""SQLAlchemy model registry used by Alembic metadata discovery."""

from app.models.assets import AssetModel
from app.models.conversations import ConversationModel, MessageModel
from app.models.posts import (
    GenerationArtifactModel,
    PostGenerationJobModel,
    PostGenerationModel,
    PostGenerationStateModel,
    PostGenerationStateVersionModel,
    PostModel,
)

__all__ = [
    "AssetModel",
    "ConversationModel",
    "GenerationArtifactModel",
    "MessageModel",
    "PostGenerationModel",
    "PostGenerationJobModel",
    "PostGenerationStateModel",
    "PostGenerationStateVersionModel",
    "PostModel",
]
