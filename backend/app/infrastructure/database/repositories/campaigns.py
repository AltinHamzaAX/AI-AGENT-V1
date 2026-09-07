from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaigns import CampaignBriefModel, CampaignModel, CampaignPlanModel
from app.models.conversations import ConversationModel
from app.modules.campaigns.domain import Campaign, CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.shared.conversations.domain import ConversationKind, ConversationScope


def _campaign(model: CampaignModel) -> Campaign:
    return Campaign(
        id=model.id,
        conversation_id=model.conversation_id,
        status=CampaignStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _brief(model: CampaignBriefModel) -> CampaignBrief:
    return CampaignBrief(
        business=model.business,
        product_or_service=model.product_or_service,
        goal=model.goal,
        audience=model.audience,
        location=model.location,
        offer=model.offer,
        value_proposition=model.value_proposition,
        channels=model.channels,
        budget_amount=model.budget_amount,
        budget_currency=model.budget_currency,
        duration=model.duration,
        brand_tone=model.brand_tone,
        constraints=model.constraints,
    )


class SQLAlchemyCampaignRepository:
    """Campaign persistence adapter; transaction ownership remains with the caller."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def conversation_exists(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> bool:
        statement = select(ConversationModel.id).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == scope.user_id,
            ConversationModel.project_id == scope.project_id,
            ConversationModel.kind == ConversationKind.CAMPAIGN.value,
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def create_campaign(
        self,
        *,
        conversation_id: UUID,
        initial_brief: CampaignBrief,
    ) -> Campaign:
        campaign_id = uuid4()
        campaign_model = CampaignModel(
            id=campaign_id,
            conversation_id=conversation_id,
        )
        self._session.add(campaign_model)
        await self._session.flush()
        brief_model = CampaignBriefModel(
            campaign_id=campaign_id,
            **initial_brief.model_dump(),
        )
        self._session.add(brief_model)
        await self._session.flush()
        await self._session.refresh(campaign_model)
        return _campaign(campaign_model)

    async def get_campaign(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None:
        model = await self._find_campaign(campaign_id=campaign_id, scope=scope)
        return _campaign(model) if model else None

    async def find_campaign_by_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None:
        statement = (
            select(CampaignModel)
            .join(ConversationModel, ConversationModel.id == CampaignModel.conversation_id)
            .where(
                CampaignModel.conversation_id == conversation_id,
                ConversationModel.user_id == scope.user_id,
                ConversationModel.project_id == scope.project_id,
                ConversationModel.kind == ConversationKind.CAMPAIGN.value,
            )
        )
        model = (await self._session.execute(statement)).scalar_one_or_none()
        return _campaign(model) if model else None

    async def get_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignBrief | None:
        if await self._find_campaign(campaign_id=campaign_id, scope=scope) is None:
            return None
        model = await self._session.get(CampaignBriefModel, campaign_id)
        return _brief(model) if model else None

    async def update_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        updates: CampaignBrief,
    ) -> CampaignBrief | None:
        if (
            await self._find_campaign(
                campaign_id=campaign_id,
                scope=scope,
                for_update=True,
            )
            is None
        ):
            return None
        model = await self._session.get(CampaignBriefModel, campaign_id)
        if model is None:
            return None
        values = updates.model_dump(exclude_unset=True)
        for field_name, value in values.items():
            setattr(model, field_name, value)
        if values:
            model.updated_at = datetime.now(UTC)
            await self._session.flush()
            await self._session.refresh(model)
        return _brief(model)

    async def get_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignPlan | None:
        if await self._find_campaign(campaign_id=campaign_id, scope=scope) is None:
            return None
        model = await self._session.get(CampaignPlanModel, campaign_id)
        return CampaignPlan.model_validate(model.data) if model else None

    async def save_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        plan: CampaignPlan,
    ) -> CampaignPlan | None:
        if (
            await self._find_campaign(
                campaign_id=campaign_id,
                scope=scope,
                for_update=True,
            )
            is None
        ):
            return None
        data = plan.model_dump(mode="json")
        model = await self._session.get(CampaignPlanModel, campaign_id)
        if model is None:
            model = CampaignPlanModel(campaign_id=campaign_id, data=data)
            self._session.add(model)
        else:
            model.data = data
            model.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(model)
        return CampaignPlan.model_validate(model.data)

    async def update_status(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        status: CampaignStatus,
    ) -> Campaign | None:
        model = await self._find_campaign(
            campaign_id=campaign_id,
            scope=scope,
            for_update=True,
        )
        if model is None:
            return None
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(model)
        return _campaign(model)

    async def _find_campaign(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        for_update: bool = False,
    ) -> CampaignModel | None:
        statement = (
            select(CampaignModel)
            .join(ConversationModel, ConversationModel.id == CampaignModel.conversation_id)
            .where(
                CampaignModel.id == campaign_id,
                ConversationModel.user_id == scope.user_id,
                ConversationModel.project_id == scope.project_id,
                ConversationModel.kind == ConversationKind.CAMPAIGN.value,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()


__all__ = ["SQLAlchemyCampaignRepository"]
