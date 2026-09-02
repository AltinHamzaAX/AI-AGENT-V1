from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.conversations import ConversationServiceDependency
from app.dependencies.providers import get_llm_provider
from app.infrastructure.database.repositories.campaigns import (
    SQLAlchemyCampaignRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.integrations.llm import LLMProvider
from app.modules.campaigns.services import (
    CampaignConversationExtractor,
    CampaignExportService,
    CampaignMessagingService,
    CampaignPlanGenerator,
    CampaignService,
)


def get_campaign_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> CampaignService:
    return CampaignService(SQLAlchemyCampaignRepository(session))


def get_campaign_conversation_extractor(
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> CampaignConversationExtractor:
    return CampaignConversationExtractor(llm)


def get_campaign_plan_generator(
    llm: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> CampaignPlanGenerator:
    return CampaignPlanGenerator(llm)


def get_campaign_export_service() -> CampaignExportService:
    return CampaignExportService()


def get_campaign_messaging_service(
    campaigns: Annotated[CampaignService, Depends(get_campaign_service)],
    conversations: ConversationServiceDependency,
    extractor: Annotated[
        CampaignConversationExtractor,
        Depends(get_campaign_conversation_extractor),
    ],
) -> CampaignMessagingService:
    return CampaignMessagingService(
        campaigns=campaigns,
        conversations=conversations,
        extractor=extractor,
    )


CampaignServiceDependency = Annotated[CampaignService, Depends(get_campaign_service)]
CampaignConversationExtractorDependency = Annotated[
    CampaignConversationExtractor,
    Depends(get_campaign_conversation_extractor),
]
CampaignMessagingServiceDependency = Annotated[
    CampaignMessagingService,
    Depends(get_campaign_messaging_service),
]
CampaignPlanGeneratorDependency = Annotated[
    CampaignPlanGenerator,
    Depends(get_campaign_plan_generator),
]
CampaignExportServiceDependency = Annotated[
    CampaignExportService,
    Depends(get_campaign_export_service),
]

__all__ = [
    "CampaignConversationExtractorDependency",
    "CampaignExportServiceDependency",
    "CampaignMessagingServiceDependency",
    "CampaignPlanGeneratorDependency",
    "CampaignServiceDependency",
    "get_campaign_conversation_extractor",
    "get_campaign_export_service",
    "get_campaign_messaging_service",
    "get_campaign_plan_generator",
    "get_campaign_service",
]
