from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.providers import get_provider_bundle
from app.infrastructure.database.repositories.campaigns import (
    SQLAlchemyCampaignRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.modules.campaigns.services import CampaignConversationExtractor, CampaignService
from app.modules.posts.providers import ProviderBundle


def get_campaign_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> CampaignService:
    return CampaignService(SQLAlchemyCampaignRepository(session))


def get_campaign_conversation_extractor(
    providers: Annotated[ProviderBundle, Depends(get_provider_bundle)],
) -> CampaignConversationExtractor:
    return CampaignConversationExtractor(providers.llm)


CampaignServiceDependency = Annotated[CampaignService, Depends(get_campaign_service)]
CampaignConversationExtractorDependency = Annotated[
    CampaignConversationExtractor,
    Depends(get_campaign_conversation_extractor),
]

__all__ = [
    "CampaignConversationExtractorDependency",
    "CampaignServiceDependency",
    "get_campaign_conversation_extractor",
    "get_campaign_service",
]
