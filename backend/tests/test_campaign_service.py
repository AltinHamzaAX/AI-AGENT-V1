from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.campaigns.domain import (
    Campaign,
    CampaignNotFoundError,
    CampaignSourceNotFoundError,
    CampaignStatus,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief
from app.modules.campaigns.services import CampaignService
from app.shared.conversations.domain import ConversationScope


@pytest.fixture
def scope() -> ConversationScope:
    return ConversationScope(user_id=uuid4(), project_id=uuid4())


@pytest.fixture
def repository() -> Mock:
    return Mock(spec=CampaignRepository)


def _campaign(*, conversation_id=None) -> Campaign:
    now = datetime.now(UTC)
    return Campaign(
        id=uuid4(),
        conversation_id=conversation_id or uuid4(),
        status=CampaignStatus.BRIEFING,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_create_campaign_validates_source_and_delegates_atomic_creation(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    conversation_id = uuid4()
    brief = CampaignBrief(business="Acme", goal="Launch a new product")
    campaign = _campaign(conversation_id=conversation_id)
    repository.conversation_exists = AsyncMock(return_value=True)
    repository.create_campaign = AsyncMock(return_value=campaign)
    service = CampaignService(repository)

    result = await service.create_campaign(
        conversation_id=conversation_id,
        scope=scope,
        initial_brief=brief,
    )

    assert result == campaign
    repository.conversation_exists.assert_awaited_once_with(
        conversation_id=conversation_id,
        scope=scope,
    )
    repository.create_campaign.assert_awaited_once_with(
        conversation_id=conversation_id,
        initial_brief=brief,
    )


@pytest.mark.asyncio
async def test_create_campaign_uses_empty_brief_when_none_is_supplied(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    conversation_id = uuid4()
    repository.conversation_exists = AsyncMock(return_value=True)
    repository.create_campaign = AsyncMock(return_value=_campaign(conversation_id=conversation_id))

    await CampaignService(repository).create_campaign(
        conversation_id=conversation_id,
        scope=scope,
    )

    persisted_brief = repository.create_campaign.await_args.kwargs["initial_brief"]
    assert persisted_brief == CampaignBrief()


@pytest.mark.asyncio
async def test_create_campaign_rejects_invalid_campaign_conversation(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    repository.conversation_exists = AsyncMock(return_value=False)
    repository.create_campaign = AsyncMock()

    with pytest.raises(CampaignSourceNotFoundError):
        await CampaignService(repository).create_campaign(
            conversation_id=uuid4(),
            scope=scope,
        )

    repository.create_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_campaign_enforces_scope_and_raises_when_missing(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    repository.get_campaign = AsyncMock(return_value=None)

    with pytest.raises(CampaignNotFoundError):
        await CampaignService(repository).get_campaign(
            campaign_id=campaign_id,
            scope=scope,
        )

    repository.get_campaign.assert_awaited_once_with(campaign_id=campaign_id, scope=scope)


@pytest.mark.asyncio
async def test_find_campaign_by_conversation_preserves_optional_result(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    conversation_id = uuid4()
    repository.find_campaign_by_conversation = AsyncMock(return_value=None)

    result = await CampaignService(repository).find_campaign_by_conversation(
        conversation_id=conversation_id,
        scope=scope,
    )

    assert result is None
    repository.find_campaign_by_conversation.assert_awaited_once_with(
        conversation_id=conversation_id,
        scope=scope,
    )


@pytest.mark.asyncio
async def test_get_brief_returns_current_brief_and_raises_when_missing(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    brief = CampaignBrief(audience="Independent retailers")
    repository.get_brief = AsyncMock(side_effect=[brief, None])
    service = CampaignService(repository)

    assert await service.get_brief(campaign_id=campaign_id, scope=scope) == brief
    with pytest.raises(CampaignNotFoundError):
        await service.get_brief(campaign_id=campaign_id, scope=scope)


@pytest.mark.asyncio
async def test_get_plan_preserves_absence_of_optional_current_plan(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    repository.get_plan = AsyncMock(return_value=None)

    result = await CampaignService(repository).get_plan(
        campaign_id=campaign_id,
        scope=scope,
    )

    assert result is None
    repository.get_plan.assert_awaited_once_with(campaign_id=campaign_id, scope=scope)
