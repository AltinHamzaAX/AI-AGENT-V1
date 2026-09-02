from datetime import datetime
from typing import Self
from uuid import UUID

from app.modules.campaigns.domain import Campaign, CampaignStatus
from app.modules.campaigns.schemas.models import (
    CampaignBrief,
    CampaignPlan,
    CampaignSchema,
    LongText,
)


class CreateCampaignRequest(CampaignSchema):
    conversation_id: UUID
    brief: CampaignBrief | None = None


class CreateCampaignResponse(CampaignSchema):
    id: UUID
    conversation_id: UUID
    status: CampaignStatus

    @classmethod
    def from_domain(cls, campaign: Campaign) -> Self:
        return cls(
            id=campaign.id,
            conversation_id=campaign.conversation_id,
            status=campaign.status,
        )


class CampaignDetailResponse(CampaignSchema):
    id: UUID
    conversation_id: UUID
    status: CampaignStatus
    brief: CampaignBrief
    plan_available: bool
    plan_outdated: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        campaign: Campaign,
        *,
        brief: CampaignBrief,
        plan_available: bool,
        plan_outdated: bool,
    ) -> Self:
        return cls(
            id=campaign.id,
            conversation_id=campaign.conversation_id,
            status=campaign.status,
            brief=brief,
            plan_available=plan_available,
            plan_outdated=plan_outdated,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )


class GenerateCampaignResponse(CampaignSchema):
    status: CampaignStatus
    plan: CampaignPlan


class CampaignMessageRequest(CampaignSchema):
    message: LongText


class CampaignMessageResponse(CampaignSchema):
    reply: LongText
    status: CampaignStatus
    brief: CampaignBrief


__all__ = [
    "CampaignDetailResponse",
    "CampaignMessageRequest",
    "CampaignMessageResponse",
    "CreateCampaignRequest",
    "CreateCampaignResponse",
    "GenerateCampaignResponse",
]
