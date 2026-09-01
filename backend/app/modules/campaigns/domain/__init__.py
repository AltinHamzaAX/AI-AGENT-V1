"""Campaign domain types and contracts."""

from app.modules.campaigns.domain.entities import Campaign
from app.modules.campaigns.domain.enums import CampaignStatus
from app.modules.campaigns.domain.exceptions import (
    CampaignNotFoundError,
    CampaignSourceNotFoundError,
    InvalidCampaignTransitionError,
)
from app.modules.campaigns.domain.state import (
    BUSINESS_REQUIREMENT,
    CampaignEvent,
    CampaignReadiness,
    evaluate_campaign_readiness,
    status_for_readiness,
    transition_campaign_status,
)

__all__ = [
    "BUSINESS_REQUIREMENT",
    "Campaign",
    "CampaignEvent",
    "CampaignNotFoundError",
    "CampaignReadiness",
    "CampaignSourceNotFoundError",
    "CampaignStatus",
    "InvalidCampaignTransitionError",
    "evaluate_campaign_readiness",
    "status_for_readiness",
    "transition_campaign_status",
]
