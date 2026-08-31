"""Campaign domain types and contracts."""

from app.modules.campaigns.domain.entities import Campaign
from app.modules.campaigns.domain.enums import CampaignStatus
from app.modules.campaigns.domain.exceptions import (
    CampaignNotFoundError,
    CampaignSourceNotFoundError,
)

__all__ = [
    "Campaign",
    "CampaignNotFoundError",
    "CampaignSourceNotFoundError",
    "CampaignStatus",
]
