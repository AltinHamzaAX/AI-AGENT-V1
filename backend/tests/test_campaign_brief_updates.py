from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.modules.campaigns.domain import CampaignNotFoundError
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignConversationResult, CampaignPlan
from app.modules.campaigns.services import CampaignService
from app.shared.conversations.domain import ConversationScope


@pytest.fixture
def scope() -> ConversationScope:
    return ConversationScope(user_id=uuid4(), project_id=uuid4())


@pytest.fixture
def repository() -> Mock:
    return Mock(spec=CampaignRepository)


@pytest.mark.asyncio
async def test_update_adds_multiple_fields_and_preserves_none_values(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    current = CampaignBrief(business="FitZone Gym", audience="Students", offer="20% off")
    expected = CampaignBrief(
        business="FitZone Gym",
        location="Prishtina",
        goal="Acquire new members",
        audience="Students",
        offer="20% off",
    )
    repository.get_brief = AsyncMock(return_value=current)
    repository.update_brief = AsyncMock(return_value=expected)
    repository.get_plan = AsyncMock(return_value=None)

    result = await CampaignService(repository).update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(
            location="Prishtina",
            goal="Acquire new members",
            audience=None,
        ),
    )

    assert result.brief == expected
    assert result.changed is True
    assert result.changed_fields == ("goal", "location")
    assert result.plan_exists is False
    patch = repository.update_brief.await_args.kwargs["updates"]
    assert patch.model_dump(exclude_unset=True) == {
        "goal": "Acquire new members",
        "location": "Prishtina",
    }
    repository.update_brief.assert_awaited_once_with(
        campaign_id=campaign_id,
        scope=scope,
        updates=patch,
    )


@pytest.mark.asyncio
async def test_explicit_non_null_correction_replaces_current_value(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    corrected = CampaignBrief(
        audience="Students",
        budget_amount=500,
        budget_currency="EUR",
    )
    repository.get_brief = AsyncMock(
        return_value=CampaignBrief(
            audience="Students",
            budget_amount=200,
            budget_currency="EUR",
        )
    )
    repository.update_brief = AsyncMock(return_value=corrected)
    repository.get_plan = AsyncMock(return_value=Mock(spec=CampaignPlan))

    result = await CampaignService(repository).update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(
            audience=None,
            budget_amount=500,
            budget_currency="EUR",
        ),
    )

    assert result.brief == corrected
    assert result.changed_fields == ("budget_amount",)
    assert result.plan_exists is True
    patch = repository.update_brief.await_args.kwargs["updates"]
    assert patch.model_dump(exclude_unset=True) == {"budget_amount": 500}
    repository.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_none_unset_identical_and_recommended_values_are_no_op(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    current = CampaignBrief(business="Acme", audience="Retailers")
    conversation = CampaignConversationResult(
        reply="TikTok could be useful.",
        extracted_fields=CampaignBrief(business="Acme", audience=None, channels=None),
    )
    repository.get_brief = AsyncMock(return_value=current)
    repository.update_brief = AsyncMock()
    repository.get_plan = AsyncMock(return_value=Mock(spec=CampaignPlan))

    result = await CampaignService(repository).update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=conversation.extracted_fields,
    )

    assert result.brief == current
    assert result.changed is False
    assert result.changed_fields == ()
    assert result.plan_exists is True
    assert result.brief.channels is None
    repository.update_brief.assert_not_awaited()
    repository.update_status.assert_not_called()


class InMemoryBriefRepository:
    def __init__(self, brief: CampaignBrief) -> None:
        self.brief = brief
        self.scopes: list[ConversationScope] = []

    async def get_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignBrief:
        self.scopes.append(scope)
        return self.brief

    async def update_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        updates: CampaignBrief,
    ) -> CampaignBrief:
        self.scopes.append(scope)
        self.brief = CampaignBrief.model_validate(
            {**self.brief.model_dump(), **updates.model_dump(exclude_unset=True)}
        )
        return self.brief

    async def get_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> None:
        self.scopes.append(scope)
        return None


@pytest.mark.asyncio
async def test_incremental_updates_build_one_valid_brief_across_turns(
    scope: ConversationScope,
) -> None:
    campaign_id = uuid4()
    repository = InMemoryBriefRepository(CampaignBrief())
    service = CampaignService(repository)

    await service.update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(business="FitZone Gym", location="Prishtina"),
    )
    await service.update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(audience="Students"),
    )
    result = await service.update_brief_from_extraction(
        campaign_id=campaign_id,
        scope=scope,
        extracted_fields=CampaignBrief(budget_amount=500, budget_currency="EUR"),
    )

    assert result.brief == CampaignBrief(
        business="FitZone Gym",
        location="Prishtina",
        audience="Students",
        budget_amount=500,
        budget_currency="EUR",
    )
    assert all(call_scope == scope for call_scope in repository.scopes)


@pytest.mark.asyncio
async def test_missing_or_out_of_scope_campaign_raises_not_found(
    repository: Mock,
    scope: ConversationScope,
) -> None:
    repository.get_brief = AsyncMock(return_value=None)
    repository.update_brief = AsyncMock()
    repository.get_plan = AsyncMock()

    with pytest.raises(CampaignNotFoundError):
        await CampaignService(repository).update_brief_from_extraction(
            campaign_id=uuid4(),
            scope=scope,
            extracted_fields=CampaignBrief(goal="Acquire customers"),
        )

    repository.update_brief.assert_not_awaited()
    repository.get_plan.assert_not_awaited()
