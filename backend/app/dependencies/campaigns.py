from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.campaigns import (
    SQLAlchemyCampaignRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.modules.campaigns.services import CampaignService


def get_campaign_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> CampaignService:
    return CampaignService(SQLAlchemyCampaignRepository(session))


CampaignServiceDependency = Annotated[CampaignService, Depends(get_campaign_service)]

__all__ = ["CampaignServiceDependency", "get_campaign_service"]
