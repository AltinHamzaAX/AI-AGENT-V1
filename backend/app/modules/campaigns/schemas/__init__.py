"""Campaign transport and application schemas."""

from app.modules.campaigns.schemas.api import CampaignMessageRequest, CampaignMessageResponse
from app.modules.campaigns.schemas.models import (
    BudgetAllocation,
    BudgetItem,
    CampaignBrief,
    CampaignConversationResult,
    CampaignPlan,
    ChannelStrategy,
    ContentDirection,
    KPI,
    Objective,
    TargetAudience,
    TimelinePhase,
)

__all__ = [
    "BudgetAllocation",
    "BudgetItem",
    "CampaignBrief",
    "CampaignConversationResult",
    "CampaignMessageRequest",
    "CampaignMessageResponse",
    "CampaignPlan",
    "ChannelStrategy",
    "ContentDirection",
    "KPI",
    "Objective",
    "TargetAudience",
    "TimelinePhase",
]
