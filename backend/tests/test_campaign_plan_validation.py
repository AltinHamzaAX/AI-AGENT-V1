from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest

from app.integrations.llm import ProviderQuotaError, ProviderResponseError
from app.modules.campaigns.domain import CampaignPlanValidationError, CampaignStatus
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services import (
    CampaignPlanGenerator,
    CampaignPlanValidator,
    CampaignService,
)
from app.shared.conversations.domain import ConversationScope
from tests.test_campaign_plan_generation import _campaign, _plan_data


def _brief() -> CampaignBrief:
    return CampaignBrief(
        business="FitZone Gym",
        product_or_service="Gym membership",
        goal="Acquire student members",
        audience="Students",
        location="Prishtina",
        offer="20% off the first month",
        value_proposition="Flexible gym access for students",
        channels=["Instagram"],
        budget_amount=300,
        budget_currency="EUR",
        duration="2 weeks",
    )


def _plan(**updates) -> CampaignPlan:
    data = _plan_data()
    data["offer"] = "20% off the first month"
    data["value_proposition"] = "Flexible gym access for students."
    data.update(updates)
    return CampaignPlan.model_validate(data)


@pytest.mark.parametrize(
    ("allocation", "issue"),
    [
        (
            {
                "total": 250,
                "currency": "EUR",
                "items": [{"channel": "Instagram", "amount": 250, "reason": "Ads"}],
            },
            "budget.total_mismatch",
        ),
        (
            {
                "total": 300,
                "currency": "USD",
                "items": [{"channel": "Instagram", "amount": 300, "reason": "Ads"}],
            },
            "budget.currency_mismatch",
        ),
        (
            {
                "total": 300,
                "currency": "EUR",
                "items": [{"channel": "Instagram", "amount": 200, "reason": "Ads"}],
            },
            "budget.items_total_mismatch",
        ),
    ],
)
def test_validator_rejects_deterministic_budget_mismatches(
    allocation: dict,
    issue: str,
) -> None:
    with pytest.raises(CampaignPlanValidationError) as captured:
        CampaignPlanValidator().validate(
            _brief(),
            _plan(budget_allocation=allocation),
        )

    assert issue in captured.value.issues


def test_validator_requires_confirmed_channels_and_accepts_recommended_additions() -> None:
    missing = _plan(
        channels=[
            {"name": "TikTok", "purpose": "Reach students", "reason": "Recommended"}
        ]
    )
    with pytest.raises(CampaignPlanValidationError) as captured:
        CampaignPlanValidator().validate(_brief(), missing)
    assert captured.value.issues == ("channels.confirmed_missing",)

    plan = _plan(
        channels=[
            {"name": "instagram", "purpose": "Confirmed channel", "reason": "Reach"},
            {"name": "TikTok", "purpose": "Additional reach", "reason": "Recommended"},
        ]
    )
    assert CampaignPlanValidator().validate(_brief(), plan) is plan


def test_validator_accepts_reasonable_semantic_paraphrases() -> None:
    brief = _brief().model_copy(
        update={
            "audience": "Students aged 18-25",
            "offer": "20% off the first month",
            "value_proposition": "Affordable access with flexible opening hours",
        }
    )
    plan = _plan(
        target_audience={
            "primary": "Students aged 18 to 25 in Prishtina",
            "location": "Prishtina, Kosovo",
            "needs_or_motivations": ["Affordable and flexible fitness options"],
        },
        offer="Students save one fifth on their first month.",
        value_proposition="Flexible opening times make fitness more affordable for students.",
    )

    assert CampaignPlanValidator().validate(brief, plan) is plan


def test_validator_rejects_clear_location_mismatch() -> None:
    plan = _plan(
        target_audience={
            "primary": "Students",
            "location": "Tirana",
            "needs_or_motivations": [],
        }
    )
    with pytest.raises(CampaignPlanValidationError) as captured:
        CampaignPlanValidator().validate(_brief(), plan)
    assert captured.value.issues == ("location.mismatch",)


def _generation_repository(
    *,
    campaign_id,
    brief: CampaignBrief,
    saved_plan: CampaignPlan | None,
) -> Mock:
    repository = Mock(spec=CampaignRepository)
    repository.get_brief = AsyncMock(return_value=brief)
    repository.get_campaign = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
        ]
    )
    repository.update_status = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY),
        ]
    )
    repository.save_plan = AsyncMock(return_value=saved_plan)
    return repository


@pytest.mark.asyncio
async def test_validation_precedes_save_and_plan_ready_follows_save() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = _brief()
    plan = _plan()
    events: list[str] = []
    repository = _generation_repository(
        campaign_id=campaign_id,
        brief=brief,
        saved_plan=plan,
    )
    generator = Mock(spec=CampaignPlanGenerator)

    async def generate(*args, **kwargs):
        events.append("generate")
        return plan

    async def save_plan(**kwargs):
        events.append("save")
        return plan

    original_update_status = repository.update_status

    async def update_status(**kwargs):
        events.append(f"status:{kwargs['status'].value}")
        return await original_update_status(**kwargs)

    validator = Mock(spec=CampaignPlanValidator)

    def validate(*args):
        events.append("validate")
        return plan

    generator.generate = AsyncMock(side_effect=generate)
    repository.save_plan = AsyncMock(side_effect=save_plan)
    repository.update_status = AsyncMock(side_effect=update_status)
    validator.validate = Mock(side_effect=validate)

    result = await CampaignService(repository, plan_validator=validator).generate_plan(
        campaign_id=campaign_id,
        scope=scope,
        generator=generator,
    )

    assert result == plan
    assert events == ["status:GENERATING", "generate", "validate", "save", "status:PLAN_READY"]


@pytest.mark.asyncio
async def test_bounded_retry_saves_only_later_valid_candidate() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = _brief()
    invalid = _plan(
        budget_allocation={
            "total": 200,
            "currency": "EUR",
            "items": [{"channel": "Instagram", "amount": 200, "reason": "Ads"}],
        }
    )
    valid = _plan()
    repository = _generation_repository(
        campaign_id=campaign_id,
        brief=brief,
        saved_plan=valid,
    )
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(side_effect=[invalid, valid])

    result = await CampaignService(repository).generate_plan(
        campaign_id=campaign_id,
        scope=scope,
        generator=generator,
    )

    assert result == valid
    assert generator.generate.await_count == 2
    assert generator.generate.await_args_list[1] == call(
        brief,
        repair_issues=("budget.total_mismatch",),
    )
    repository.save_plan.assert_awaited_once_with(
        campaign_id=campaign_id,
        scope=scope,
        plan=valid,
    )


@pytest.mark.asyncio
async def test_bounded_retry_stops_and_restores_ready_without_persisting() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = _brief()
    invalid = _plan(channels=[])
    repository = _generation_repository(
        campaign_id=campaign_id,
        brief=brief,
        saved_plan=None,
    )
    repository.update_status.side_effect = [
        _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
        _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
    ]
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(side_effect=[invalid, invalid])

    with pytest.raises(CampaignPlanValidationError):
        await CampaignService(repository).generate_plan(
            campaign_id=campaign_id,
            scope=scope,
            generator=generator,
        )

    assert generator.generate.await_count == CampaignService.MAX_PLAN_GENERATION_ATTEMPTS
    repository.save_plan.assert_not_awaited()
    assert repository.update_status.await_args_list[-1].kwargs["status"] is CampaignStatus.READY
    assert await repository.get_brief(campaign_id=campaign_id, scope=scope) == brief


@pytest.mark.asyncio
async def test_structural_output_failure_retries_but_quota_failure_does_not() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = _brief()

    for failure, expected_calls in [
        (ProviderResponseError("invalid structured output"), 2),
        (ProviderQuotaError("quota exhausted"), 1),
    ]:
        repository = _generation_repository(
            campaign_id=campaign_id,
            brief=brief,
            saved_plan=None,
        )
        repository.update_status.side_effect = [
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
        ]
        generator = Mock(spec=CampaignPlanGenerator)
        generator.generate = AsyncMock(side_effect=failure)

        with pytest.raises(type(failure)):
            await CampaignService(repository).generate_plan(
                campaign_id=campaign_id,
                scope=scope,
                generator=generator,
            )

        assert generator.generate.await_count == expected_calls
        repository.save_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistence_failure_never_transitions_to_plan_ready() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = _brief()
    plan = _plan()
    repository = _generation_repository(
        campaign_id=campaign_id,
        brief=brief,
        saved_plan=None,
    )
    repository.save_plan = AsyncMock(side_effect=RuntimeError("database unavailable"))
    repository.update_status.side_effect = [
        _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
        _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
    ]
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(return_value=plan)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await CampaignService(repository).generate_plan(
            campaign_id=campaign_id,
            scope=scope,
            generator=generator,
        )

    statuses = [item.kwargs["status"] for item in repository.update_status.await_args_list]
    assert statuses == [CampaignStatus.GENERATING, CampaignStatus.READY]
    assert await repository.get_brief(campaign_id=campaign_id, scope=scope) == brief


@pytest.mark.asyncio
async def test_outdated_plan_is_hidden_until_explicit_regeneration_replaces_it() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    old_plan = _plan(campaign_name="Old Plan")
    latest_brief = _brief().model_copy(update={"offer": "30% off the first month"})
    new_plan = _plan(campaign_name="New Plan", offer="30% off the first month")
    repository = Mock(spec=CampaignRepository)
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.READY)
    )
    repository.get_plan = AsyncMock(return_value=old_plan)
    service = CampaignService(repository)

    assert await service.get_plan(campaign_id=campaign_id, scope=scope) is None
    repository.get_plan.assert_not_awaited()

    repository.get_brief = AsyncMock(return_value=latest_brief)
    repository.get_campaign = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
        ]
    )
    repository.update_status = AsyncMock(
        side_effect=[
            _campaign(campaign_id=campaign_id, status=CampaignStatus.GENERATING),
            _campaign(campaign_id=campaign_id, status=CampaignStatus.PLAN_READY),
        ]
    )
    repository.save_plan = AsyncMock(return_value=new_plan)
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(return_value=new_plan)

    result = await service.generate_plan(
        campaign_id=campaign_id,
        scope=scope,
        generator=generator,
    )

    assert result == new_plan
    generator.generate.assert_awaited_once_with(latest_brief, repair_issues=())
    repository.save_plan.assert_awaited_once_with(
        campaign_id=campaign_id,
        scope=scope,
        plan=new_plan,
    )
