from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.models.campaigns import CampaignBriefModel, CampaignModel, CampaignPlanModel
from app.models.conversations import ConversationModel
from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas import CampaignPlan


@pytest_asyncio.fixture
async def campaign_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


def _plan_data() -> dict[str, object]:
    return CampaignPlan(
        campaign_name="Student Fitness Boost",
        executive_summary="A focused student acquisition campaign.",
        objective={"primary": "Acquire new customers", "secondary": None},
        target_audience={
            "primary": "Students aged 18-25",
            "location": "Prishtina",
            "needs_or_motivations": ["Affordable access"],
        },
        offer=None,
        value_proposition="Flexible and affordable gym access.",
        positioning="The accessible gym for active students.",
        key_message="Build your routine without stretching your budget.",
        strategy="Reach students with relevant short-form content.",
        channels=[
            {"name": "Instagram", "purpose": "Reach students", "reason": "Audience fit"}
        ],
        content_direction=[{"idea": "Routine stories", "purpose": "Demonstrate fit"}],
        budget_allocation=None,
        timeline=[
            {
                "period": "Week 1",
                "phase": "Launch",
                "objective": "Build awareness",
                "activities": ["Publish launch content"],
            }
        ],
        kpis=[{"name": "Membership inquiries", "purpose": "Measure intent"}],
        assumptions_or_risks=[],
        next_steps=["Confirm creative assets"],
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_campaign_models_persist_partial_brief_and_current_plan(
    campaign_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = uuid4()
    campaign_id = uuid4()
    plan_data = _plan_data()

    async with campaign_session_factory.begin() as session:
        session.add(
            ConversationModel(
                id=conversation_id,
                user_id=uuid4(),
                project_id=uuid4(),
                kind="campaign",
            )
        )
        session.add(CampaignModel(id=campaign_id, conversation_id=conversation_id))
        session.add(
            CampaignBriefModel(
                campaign_id=campaign_id,
                business="FitZone Gym",
                audience="Students",
                channels=None,
                budget_amount=Decimal("200.50"),
                constraints=[],
            )
        )
        session.add(CampaignPlanModel(campaign_id=campaign_id, data=plan_data))

    async with campaign_session_factory() as session:
        campaign = await session.get(CampaignModel, campaign_id)
        brief = await session.get(CampaignBriefModel, campaign_id)
        plan = await session.get(CampaignPlanModel, campaign_id)

    assert campaign is not None
    assert campaign.status == CampaignStatus.BRIEFING.value
    assert campaign.created_at is not None
    assert campaign.updated_at is not None
    assert brief is not None
    assert brief.business == "FitZone Gym"
    assert brief.product_or_service is None
    assert brief.channels is None
    assert brief.constraints == []
    assert brief.budget_amount == Decimal("200.50")
    assert plan is not None
    assert CampaignPlan.model_validate(plan.data).model_dump(mode="json") == plan_data


@pytest.mark.asyncio
async def test_one_campaign_per_conversation_is_enforced(
    campaign_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = uuid4()
    async with campaign_session_factory.begin() as session:
        session.add(
            ConversationModel(
                id=conversation_id,
                user_id=uuid4(),
                project_id=uuid4(),
                kind="campaign",
            )
        )
        session.add(CampaignModel(conversation_id=conversation_id))

    with pytest.raises(IntegrityError):
        async with campaign_session_factory.begin() as session:
            session.add(CampaignModel(conversation_id=conversation_id))


@pytest.mark.asyncio
async def test_campaign_status_and_budget_constraints_are_enforced(
    campaign_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = uuid4()
    campaign_id = uuid4()
    async with campaign_session_factory.begin() as session:
        session.add(
            ConversationModel(
                id=conversation_id,
                user_id=uuid4(),
                project_id=uuid4(),
                kind="campaign",
            )
        )

    with pytest.raises(IntegrityError):
        async with campaign_session_factory.begin() as session:
            session.add(
                CampaignModel(
                    id=campaign_id,
                    conversation_id=conversation_id,
                    status="UNKNOWN",
                )
            )

    async with campaign_session_factory.begin() as session:
        session.add(CampaignModel(id=campaign_id, conversation_id=conversation_id))

    with pytest.raises(IntegrityError):
        async with campaign_session_factory.begin() as session:
            session.add(
                CampaignBriefModel(
                    campaign_id=campaign_id,
                    budget_amount=Decimal("-1"),
                )
            )


@pytest.mark.asyncio
async def test_conversation_delete_cascades_campaign_brief_and_plan(
    campaign_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = uuid4()
    campaign_id = uuid4()
    async with campaign_session_factory.begin() as session:
        session.add(
            ConversationModel(
                id=conversation_id,
                user_id=uuid4(),
                project_id=uuid4(),
                kind="campaign",
            )
        )
        session.add(CampaignModel(id=campaign_id, conversation_id=conversation_id))
        session.add(CampaignBriefModel(campaign_id=campaign_id))
        session.add(CampaignPlanModel(campaign_id=campaign_id, data=_plan_data()))

    async with campaign_session_factory.begin() as session:
        await session.execute(
            delete(ConversationModel).where(ConversationModel.id == conversation_id)
        )

    async with campaign_session_factory() as session:
        counts = [
            await session.scalar(select(func.count(model.campaign_id)))
            for model in (CampaignBriefModel, CampaignPlanModel)
        ]
        campaign_count = await session.scalar(select(func.count(CampaignModel.id)))

    assert campaign_count == 0
    assert counts == [0, 0]
