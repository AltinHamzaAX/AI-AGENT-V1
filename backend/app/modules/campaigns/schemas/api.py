from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas.models import CampaignBrief, CampaignSchema, LongText


class CampaignMessageRequest(CampaignSchema):
    message: LongText


class CampaignMessageResponse(CampaignSchema):
    reply: LongText
    status: CampaignStatus
    brief: CampaignBrief


__all__ = ["CampaignMessageRequest", "CampaignMessageResponse"]
