from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.modules.campaigns.domain import (
    BUSINESS_REQUIREMENT,
    Campaign,
    CampaignEvent,
    CampaignStatus,
    InvalidCampaignTransitionError,
    evaluate_campaign_readiness,
    transition_campaign_status,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services import CampaignService
from app.shared.conversations.domain import ConversationScope


def _campaign(*, campaign_id: UUID, status: CampaignStatus) -> Campaign:
    now = datetime.now(UTC)
    return Campaign(
        id=campaign_id,
        conversation_id=uuid4(),
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("subject", [{"business": "Acme"}, {"product_or_service": "Coffee"}])
def test_minimum_required_context_is_ready_with_either_subject(subject: dict) -> None:
    readiness = evaluate_campaign_readiness(
        CampaignBrief(**subject, goal="Increase sales", audience="Retailers")
    )

    assert readiness.ready is True
    assert readiness.missing_fields == ()


def test_readiness_reports_missing_or_requirement_goal_and_audience() -> None:
    readiness = evaluate_campaign_readiness(CampaignBrief())

    assert readiness.ready is False
    assert readiness.missing_fields == (BUSINESS_REQUIREMENT, "goal", "audience")
    assert evaluate_campaign_readiness(
        CampaignBrief(business="Acme", audience="Retailers")
    ).missing_fields == ("goal",)
    assert evaluate_campaign_readiness(
        CampaignBrief(business="Acme", goal="Grow")
    ).missing_fields == ("audience",)


def test_optional_and_context_fields_do_not_block_readiness() -> None:
    brief = CampaignBrief(
        product_or_service="Coffee",
        goal="Increase subscriptions",
        audience="Office workers",
        location=None,
        duration=None,
        offer=None,
        channels=None,
        budget_amount=None,
        brand_tone=None,
        constraints=None,
    )

    assert evaluate_campaign_readiness(brief).ready is True


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (CampaignStatus.READY, CampaignEvent.GENERATION_REQUESTED, CampaignStatus.GENERATING),
        (CampaignStatus.GENERATING, CampaignEvent.PLAN_PERSISTED, CampaignStatus.PLAN_READY),
        (CampaignStatus.GENERATING, CampaignEvent.GENERATION_FAILED, CampaignStatus.READY),
        (CampaignStatus.PLAN_READY, CampaignEvent.EXPORTED, CampaignStatus.PLAN_READY),
    ],
)
def test_approved_lifecycle_transitions(
    current: CampaignStatus,
    event: CampaignEvent,
    expected: CampaignStatus,
) -> None:
    assert transition_campaign_status(current, event) is expected


@pytest.mark.parametrize(
    ("current", "event"),
    [
        (CampaignStatus.BRIEFING, CampaignEvent.GENERATION_REQUESTED),
        (CampaignStatus.PLAN_READY, CampaignEvent.GENERATION_REQUESTED),
        (CampaignStatus.READY, CampaignEvent.PLAN_PERSISTED),
        (CampaignStatus.BRIEFING, CampaignEvent.GENERATION_FAILED),
    ],
)
def test_invalid_lifecycle_transitions_are_rejected(
    current: CampaignStatus,
    event: CampaignEvent,
) -> None:
    with pytest.raises(InvalidCampaignTransitionError):
        transition_campaign_status(current, event)


@pytest.mark.asyncio
async def test_readiness_reevaluation_persists_briefing_ready_and_ready_briefing() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    repository.get_brief = AsyncMock(
        side_effect=[
            CampaignBrief(business="Acme", goal="Grow", audience="Retailers"),
            CampaignBrief(business="Acme", audience="Retailers"),
        ]
    )
    repository.get_campaign = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
        ]
    )
    repository.update_status = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING),
        ]
    )
    service = CampaignService(repository)

    ready = await service.reevaluate_readiness(campaign_id=campaign_id, scope=scope)
    briefing = await service.reevaluate_readiness(campaign_id=campaign_id, scope=scope)

    assert ready.campaign.status is CampaignStatus.READY
    assert ready.status_changed is True
    assert briefing.campaign.status is CampaignStatus.BRIEFING
    assert briefing.readiness.missing_fields == ("goal",)
    assert repository.update_status.await_args_list[0].kwargs["status"] is CampaignStatus.READY
    assert repository.update_status.await_args_list[1].kwargs["status"] is CampaignStatus.BRIEFING


@pytest.mark.asyncio
async def test_insufficient_briefing_campaign_remains_briefing_without_status_write() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    repository.get_brief = AsyncMock(return_value=CampaignBrief(business="Acme"))
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING)
    )
    repository.update_status = AsyncMock()

    result = await CampaignService(repository).reevaluate_readiness(
        campaign_id=campaign_id,
        scope=scope,
    )

    assert result.campaign.status is CampaignStatus.BRIEFING
    assert result.readiness.ready is False
    assert result.status_changed is False
    repository.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_ready_brief_change_logically_invalidates_plan_without_deleting_it() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    updated_brief = CampaignBrief(
        business="Acme",
        goal="Increase sales",
        audience="Retailers",
        budget_amount=500,
    )
    repository.get_brief = AsyncMock(
        return_value=updated_brief.model_copy(update={"budget_amount": 200})
    )
    repository.update_brief = AsyncMock(return_value=updated_brief)
    repository.get_plan = AsyncMock(return_value=Mock(spec=CampaignPlan))
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY)
    )
    repository.update_status = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.READY)
    )

    result = await CampaignService(repository).update_brief_and_reevaluate(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(budget_amount=500),
    )

    assert result.update.changed is True
    assert result.update.plan_exists is True
    assert result.campaign.status is CampaignStatus.READY
    assert result.readiness.ready is True
    assert result.status_changed is True
    repository.update_status.assert_awaited_once_with(
        campaign_id=campaign_id,
        scope=scope,
        status=CampaignStatus.READY,
    )
    repository.save_plan.assert_not_called()


@pytest.mark.asyncio
async def test_plan_ready_changed_insufficient_brief_returns_to_briefing() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    repository.get_brief = AsyncMock(return_value=CampaignBrief(business="Acme"))
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY)
    )
    repository.update_status = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING)
    )

    result = await CampaignService(repository).reevaluate_readiness(
        campaign_id=campaign_id,
        scope=scope,
        brief_changed=True,
    )

    assert result.campaign.status is CampaignStatus.BRIEFING
    assert result.readiness.ready is False
    assert result.status_changed is True


@pytest.mark.asyncio
async def test_no_op_plan_ready_update_does_not_change_status() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    brief = CampaignBrief(business="Acme", goal="Grow", audience="Retailers")
    campaign = _campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY)
    repository.get_brief = AsyncMock(return_value=brief)
    repository.update_brief = AsyncMock()
    repository.get_plan = AsyncMock(return_value=Mock(spec=CampaignPlan))
    repository.get_campaign = AsyncMock(return_value=campaign)
    repository.update_status = AsyncMock()

    result = await CampaignService(repository).update_brief_and_reevaluate(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(business="Acme"),
    )

    assert result.update.changed is False
    assert result.campaign.status is CampaignStatus.PLAN_READY
    assert result.status_changed is False
    repository.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_generating_campaign_rejects_brief_change_before_persistence() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    current = CampaignBrief(
        business="Acme",
        goal="Grow",
        audience="Retailers",
        budget_amount=200,
    )
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING)
    )
    repository.get_brief = AsyncMock(return_value=current)
    repository.update_brief = AsyncMock()
    repository.get_plan = AsyncMock()
    repository.update_status = AsyncMock()

    with pytest.raises(InvalidCampaignTransitionError):
        await CampaignService(repository).update_brief_and_reevaluate(
            campaign_id=campaign_id,
            scope=scope,
            extracted_fields=CampaignBrief(budget_amount=500),
        )

    assert current.budget_amount == 200
    repository.update_brief.assert_not_awaited()
    repository.get_plan.assert_not_awaited()
    repository.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_generating_campaign_allows_no_op_extraction_without_state_change() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    current = CampaignBrief(
        business="Acme",
        goal="Grow",
        audience="Retailers",
        budget_amount=200,
    )
    campaign = _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING)
    repository.get_campaign = AsyncMock(return_value=campaign)
    repository.get_brief = AsyncMock(return_value=current)
    repository.update_brief = AsyncMock()
    repository.get_plan = AsyncMock(return_value=None)
    repository.update_status = AsyncMock()

    result = await CampaignService(repository).update_brief_and_reevaluate(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(budget_amount=200, audience=None),
    )

    assert result.update.changed is False
    assert result.update.brief == current
    assert result.campaign.status is CampaignStatus.GENERATING
    assert result.status_changed is False
    repository.update_brief.assert_not_awaited()
    repository.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_persists_generation_lifecycle_and_export_is_no_op() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    repository.get_campaign = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY),
        ]
    )
    repository.update_status = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
        ]
    )
    service = CampaignService(repository)

    generating = await service.transition(
        campaign_id=campaign_id,
        scope=scope,
        event=CampaignEvent.GENERATION_REQUESTED,
    )
    completed = await service.transition(
        campaign_id=campaign_id,
        scope=scope,
        event=CampaignEvent.PLAN_PERSISTED,
    )
    recovered = await service.transition(
        campaign_id=campaign_id,
        scope=scope,
        event=CampaignEvent.GENERATION_FAILED,
    )
    exported = await service.transition(
        campaign_id=campaign_id,
        scope=scope,
        event=CampaignEvent.EXPORTED,
    )

    assert generating.status is CampaignStatus.GENERATING
    assert completed.status is CampaignStatus.PLAN_READY
    assert recovered.status is CampaignStatus.READY
    assert exported.status is CampaignStatus.PLAN_READY
    assert repository.update_status.await_count == 3


@pytest.mark.asyncio
async def test_service_rejects_invalid_transition_without_persisting_status() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    repository = Mock(spec=CampaignRepository)
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING)
    )
    repository.update_status = AsyncMock()

    with pytest.raises(InvalidCampaignTransitionError):
        await CampaignService(repository).transition(
            campaign_id=campaign_id,
            scope=scope,
            event=CampaignEvent.GENERATION_REQUESTED,
        )

    repository.update_status.assert_not_awaited()
