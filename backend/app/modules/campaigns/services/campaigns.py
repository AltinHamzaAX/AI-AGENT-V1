from dataclasses import dataclass
from uuid import UUID

from app.modules.campaigns.domain import (
    Campaign,
    CampaignNotFoundError,
    CampaignSourceNotFoundError,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.shared.conversations.domain import ConversationScope


@dataclass(frozen=True, slots=True)
class CampaignBriefUpdateResult:
    brief: CampaignBrief
    changed: bool
    changed_fields: tuple[str, ...]
    plan_exists: bool


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

    async def update_brief_from_extraction(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        extracted_fields: CampaignBrief,
    ) -> CampaignBriefUpdateResult:
        current = await self._repository.get_brief(
            campaign_id=campaign_id,
            scope=scope,
        )
        if current is None:
            raise CampaignNotFoundError

        changes = {
            name: candidate
            for name, candidate in extracted_fields.model_dump().items()
            if candidate is not None and candidate != getattr(current, name)
        }
        if changes:
            # Validate the complete merged contract before sending a partial patch.
            CampaignBrief.model_validate({**current.model_dump(), **changes})
            updated = await self._repository.update_brief(
                campaign_id=campaign_id,
                scope=scope,
                updates=CampaignBrief.model_validate(changes),
            )
            if updated is None:
                raise CampaignNotFoundError
        else:
            updated = current

        plan = await self._repository.get_plan(
            campaign_id=campaign_id,
            scope=scope,
        )
        return CampaignBriefUpdateResult(
            brief=updated,
            changed=bool(changes),
            changed_fields=tuple(changes),
            plan_exists=plan is not None,
        )


__all__ = ["CampaignBriefUpdateResult", "CampaignService"]
