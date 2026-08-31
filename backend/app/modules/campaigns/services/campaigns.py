from uuid import UUID

from app.modules.campaigns.domain import (
    Campaign,
    CampaignNotFoundError,
    CampaignSourceNotFoundError,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.shared.conversations.domain import ConversationScope


class CampaignService:
    def __init__(self, repository: CampaignRepository) -> None:
        self._repository = repository

    async def create_campaign(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        initial_brief: CampaignBrief | None = None,
    ) -> Campaign:
        if not await self._repository.conversation_exists(
            conversation_id=conversation_id,
            scope=scope,
        ):
            raise CampaignSourceNotFoundError
        return await self._repository.create_campaign(
            conversation_id=conversation_id,
            initial_brief=initial_brief or CampaignBrief(),
        )

    async def get_campaign(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> Campaign:
        campaign = await self._repository.get_campaign(
            campaign_id=campaign_id,
            scope=scope,
        )
        if campaign is None:
            raise CampaignNotFoundError
        return campaign

    async def find_campaign_by_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None:
        return await self._repository.find_campaign_by_conversation(
            conversation_id=conversation_id,
            scope=scope,
        )

    async def get_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignBrief:
        brief = await self._repository.get_brief(
            campaign_id=campaign_id,
            scope=scope,
        )
        if brief is None:
            raise CampaignNotFoundError
        return brief

    async def get_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignPlan | None:
        return await self._repository.get_plan(
            campaign_id=campaign_id,
            scope=scope,
        )


__all__ = ["CampaignService"]
