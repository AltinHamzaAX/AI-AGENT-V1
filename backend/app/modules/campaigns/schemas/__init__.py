"""Campaign transport and application schemas."""

from app.modules.campaigns.schemas.api import (
    CampaignDetailResponse,
    CampaignMessageRequest,
    CampaignMessageResponse,
    CreateCampaignRequest,
    CreateCampaignResponse,
    GenerateCampaignResponse,
)
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
    "CampaignDetailResponse",
    "CampaignMessageRequest",
    "CampaignMessageResponse",
    "CampaignPlan",
    "ChannelStrategy",
    "ContentDirection",
    "CreateCampaignRequest",
    "CreateCampaignResponse",
    "GenerateCampaignResponse",
    "KPI",
    "Objective",
    "TargetAudience",
    "TimelinePhase",
]
