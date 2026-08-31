from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.campaigns.domain.enums import CampaignStatus


@dataclass(frozen=True, slots=True)
class Campaign:
    id: UUID
    conversation_id: UUID
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime


__all__ = ["Campaign"]
