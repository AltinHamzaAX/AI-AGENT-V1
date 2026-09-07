"""SQLAlchemy model registry used by Alembic metadata discovery."""

from app.models.assets import AssetModel
from app.models.campaigns import CampaignBriefModel, CampaignModel, CampaignPlanModel
from app.models.conversations import ConversationModel, MessageModel
from app.models.posts import (
    GenerationArtifactModel,
    PostExecutionTraceModel,
    PostGenerationJobModel,
    PostGenerationModel,
    PostGenerationStateModel,
    PostGenerationStateVersionModel,
    PostModel,
    PostSemanticMemoryModel,
)

__all__ = [
    "AssetModel",
    "CampaignBriefModel",
    "CampaignModel",
    "CampaignPlanModel",
    "ConversationModel",
    "GenerationArtifactModel",
    "MessageModel",
    "PostGenerationModel",
    "PostGenerationJobModel",
    "PostGenerationStateModel",
    "PostGenerationStateVersionModel",
    "PostExecutionTraceModel",
    "PostModel",
    "PostSemanticMemoryModel",
]
