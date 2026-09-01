from dataclasses import dataclass
from uuid import UUID

from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief
from app.modules.campaigns.services.campaigns import CampaignService
from app.modules.campaigns.services.conversation import CampaignConversationExtractor
from app.shared.conversations.domain import ConversationScope, Message, MessageRole
from app.shared.conversations.service import ConversationService


@dataclass(frozen=True, slots=True)
class CampaignMessageResult:
    reply: str
    status: CampaignStatus
    brief: CampaignBrief
    user_message: Message
    assistant_message: Message


class CampaignMessagingService:
    """Coordinate one persisted Campaign conversation turn."""

    def __init__(
        self,
        *,
        campaigns: CampaignService,
        conversations: ConversationService,
        extractor: CampaignConversationExtractor,
    ) -> None:
        self._campaigns = campaigns
        self._conversations = conversations
        self._extractor = extractor

    async def reply(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        message: str,
    ) -> CampaignMessageResult:
        campaign = await self._campaigns.get_campaign(
            campaign_id=campaign_id,
            scope=scope,
        )
        brief = await self._campaigns.get_brief(
            campaign_id=campaign_id,
            scope=scope,
        )
        user_message = await self._conversations.append_message(
            conversation_id=campaign.conversation_id,
            scope=scope,
            role=MessageRole.USER,
            content=message,
        )
        conversation_result = await self._extractor.respond(
            message=message,
            current_brief=brief,
        )
        state = await self._campaigns.update_brief_and_reevaluate(
            campaign_id=campaign_id,
            scope=scope,
            extracted_fields=conversation_result.extracted_fields,
        )
        assistant_message = await self._conversations.append_message(
            conversation_id=campaign.conversation_id,
            scope=scope,
            role=MessageRole.ASSISTANT,
            content=conversation_result.reply,
        )
        return CampaignMessageResult(
            reply=conversation_result.reply,
            status=state.campaign.status,
            brief=state.update.brief,
            user_message=user_message,
            assistant_message=assistant_message,
        )


__all__ = ["CampaignMessageResult", "CampaignMessagingService"]
