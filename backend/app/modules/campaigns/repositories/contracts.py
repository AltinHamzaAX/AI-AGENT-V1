from typing import Protocol
from uuid import UUID

from app.modules.campaigns.domain import Campaign, CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.shared.conversations.domain import ConversationScope


class CampaignRepository(Protocol):
    async def conversation_exists(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> bool: ...

    async def create_campaign(
        self,
        *,
        conversation_id: UUID,
        initial_brief: CampaignBrief,
    ) -> Campaign: ...

    async def get_campaign(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None: ...

    async def find_campaign_by_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None: ...

    async def get_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignBrief | None: ...

    async def update_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        updates: CampaignBrief,
    ) -> CampaignBrief | None: ...

    async def get_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignPlan | None: ...

    async def save_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        plan: CampaignPlan,
    ) -> CampaignPlan | None: ...

    async def update_status(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        status: CampaignStatus,
    ) -> Campaign | None: ...


__all__ = ["CampaignRepository"]
