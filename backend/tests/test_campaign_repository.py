from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.campaigns import SQLAlchemyCampaignRepository
from app.models.campaigns import CampaignBriefModel, CampaignModel, CampaignPlanModel
from app.models.conversations import ConversationModel
from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.shared.conversations.domain import ConversationKind, ConversationScope


@pytest_asyncio.fixture
async def campaign_repository_session_factory(
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


async def _conversation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scope: ConversationScope,
    kind: ConversationKind = ConversationKind.CAMPAIGN,
) -> ConversationModel:
    async with session_factory.begin() as session:
        model = ConversationModel(
            user_id=scope.user_id,
            project_id=scope.project_id,
            kind=kind.value,
        )
        session.add(model)
        await session.flush()
        await session.refresh(model)
        return model


def _plan(*, campaign_name: str = "Student Fitness Boost") -> CampaignPlan:
    return CampaignPlan(
        campaign_name=campaign_name,
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
        channels=[],
        content_direction=[],
        budget_allocation=None,
        timeline=[],
        kpis=[],
        assumptions_or_risks=[],
        next_steps=["Confirm creative assets"],
    )


@pytest.mark.asyncio
async def test_repository_verifies_campaign_conversation_kind_and_scope(
    campaign_repository_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    campaign_conversation = await _conversation(
        campaign_repository_session_factory,
        scope=scope,
    )
    post_conversation = await _conversation(
        campaign_repository_session_factory,
        scope=scope,
        kind=ConversationKind.POST,
    )

    async with campaign_repository_session_factory() as session:
        repository = SQLAlchemyCampaignRepository(session)
        assert await repository.conversation_exists(
            conversation_id=campaign_conversation.id,
            scope=scope,
        )
        assert not await repository.conversation_exists(
            conversation_id=post_conversation.id,
            scope=scope,
        )
        assert not await repository.conversation_exists(
            conversation_id=campaign_conversation.id,
            scope=ConversationScope(user_id=uuid4(), project_id=scope.project_id),
        )


@pytest.mark.asyncio
async def test_repository_campaign_brief_plan_and_status_lifecycle(
    campaign_repository_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    conversation = await _conversation(campaign_repository_session_factory, scope=scope)
    initial_brief = CampaignBrief(
        business="FitZone Gym",
        audience="Students",
        offer="20% off",
    )

    async with campaign_repository_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        campaign = await repository.create_campaign(
            conversation_id=conversation.id,
            initial_brief=initial_brief,
        )
        assert campaign.status is CampaignStatus.BRIEFING

    async with campaign_repository_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        assert await repository.get_campaign(campaign_id=campaign.id, scope=scope) == campaign
        assert (
            await repository.find_campaign_by_conversation(
                conversation_id=conversation.id,
                scope=scope,
            )
            == campaign
        )
        assert await repository.get_brief(campaign_id=campaign.id, scope=scope) == initial_brief

        updated_brief = await repository.update_brief(
            campaign_id=campaign.id,
            scope=scope,
            updates=CampaignBrief(goal="Acquire customers", offer=None),
        )
        assert updated_brief is not None
        assert updated_brief.business == "FitZone Gym"
        assert updated_brief.audience == "Students"
        assert updated_brief.goal == "Acquire customers"
        assert updated_brief.offer is None

        assert await repository.get_plan(campaign_id=campaign.id, scope=scope) is None
        first_plan = _plan()
        assert await repository.save_plan(
            campaign_id=campaign.id,
            scope=scope,
            plan=first_plan,
        ) == first_plan
        replacement = _plan(campaign_name="Student Fitness Boost Revised")
        assert await repository.save_plan(
            campaign_id=campaign.id,
            scope=scope,
            plan=replacement,
        ) == replacement
        assert await repository.get_plan(campaign_id=campaign.id, scope=scope) == replacement

        updated_campaign = await repository.update_status(
            campaign_id=campaign.id,
            scope=scope,
            status=CampaignStatus.READY,
        )
        assert updated_campaign is not None
        assert updated_campaign.status is CampaignStatus.READY

    async with campaign_repository_session_factory() as session:
        plan_count = await session.scalar(select(func.count(CampaignPlanModel.campaign_id)))
    assert plan_count == 1


@pytest.mark.asyncio
async def test_repository_filters_campaign_data_by_conversation_scope(
    campaign_repository_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    wrong_scope = ConversationScope(user_id=uuid4(), project_id=scope.project_id)
    conversation = await _conversation(campaign_repository_session_factory, scope=scope)
    async with campaign_repository_session_factory.begin() as session:
        campaign = await SQLAlchemyCampaignRepository(session).create_campaign(
            conversation_id=conversation.id,
            initial_brief=CampaignBrief(business="FitZone"),
        )

    async with campaign_repository_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        assert await repository.get_campaign(campaign_id=campaign.id, scope=wrong_scope) is None
        assert await repository.get_brief(campaign_id=campaign.id, scope=wrong_scope) is None
        assert await repository.get_plan(campaign_id=campaign.id, scope=wrong_scope) is None
        assert (
            await repository.update_brief(
                campaign_id=campaign.id,
                scope=wrong_scope,
                updates=CampaignBrief(goal="Must not persist"),
            )
            is None
        )
        assert (
            await repository.save_plan(
                campaign_id=campaign.id,
                scope=wrong_scope,
                plan=_plan(),
            )
            is None
        )
        assert (
            await repository.update_status(
                campaign_id=campaign.id,
                scope=wrong_scope,
                status=CampaignStatus.READY,
            )
            is None
        )


@pytest.mark.asyncio
async def test_campaign_and_initial_brief_roll_back_together(
    campaign_repository_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    conversation = await _conversation(campaign_repository_session_factory, scope=scope)
    campaign_id = None

    with pytest.raises(RuntimeError, match="force rollback"):
        async with campaign_repository_session_factory.begin() as session:
            campaign = await SQLAlchemyCampaignRepository(session).create_campaign(
                conversation_id=conversation.id,
                initial_brief=CampaignBrief(business="Must roll back"),
            )
            campaign_id = campaign.id
            raise RuntimeError("force rollback")

    assert campaign_id is not None
    async with campaign_repository_session_factory() as session:
        campaign_count = await session.scalar(
            select(func.count(CampaignModel.id)).where(CampaignModel.id == campaign_id)
        )
        brief_count = await session.scalar(
            select(func.count(CampaignBriefModel.campaign_id)).where(
                CampaignBriefModel.campaign_id == campaign_id
            )
        )
    assert campaign_count == 0
    assert brief_count == 0


@pytest.mark.asyncio
async def test_repository_propagates_database_errors_without_partial_creation(
    campaign_repository_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    conversation = await _conversation(campaign_repository_session_factory, scope=scope)
    async with campaign_repository_session_factory.begin() as session:
        await SQLAlchemyCampaignRepository(session).create_campaign(
            conversation_id=conversation.id,
            initial_brief=CampaignBrief(business="First campaign"),
        )

    with pytest.raises(IntegrityError):
        async with campaign_repository_session_factory.begin() as session:
            await SQLAlchemyCampaignRepository(session).create_campaign(
                conversation_id=conversation.id,
                initial_brief=CampaignBrief(business="Duplicate campaign"),
            )

    async with campaign_repository_session_factory() as session:
        campaign_count = await session.scalar(select(func.count(CampaignModel.id)))
        brief_count = await session.scalar(select(func.count(CampaignBriefModel.campaign_id)))
    assert campaign_count == 1
    assert brief_count == 1
