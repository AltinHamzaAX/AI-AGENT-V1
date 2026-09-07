import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.integrations.llm import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderResponseError,
)
from app.modules.campaigns.domain import (
    Campaign,
    CampaignStatus,
    InvalidCampaignTransitionError,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services import CampaignPlanGenerator, CampaignService
from app.shared.conversations.domain import ConversationScope


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []
        self.failure: Exception | None = None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return LLMResponse(text=self.response, provider="fake", model="fake-model")


def _campaign(*, campaign_id: UUID, status: CampaignStatus) -> Campaign:
    now = datetime.now(UTC)
    return Campaign(
        id=campaign_id,
        conversation_id=uuid4(),
        status=status,
        created_at=now,
        updated_at=now,
    )


def _plan_data() -> dict:
    return {
        "campaign_name": "Student Fitness Boost",
        "executive_summary": "A focused student acquisition campaign.",
        "objective": {"primary": "Acquire student members", "secondary": None},
        "target_audience": {
            "primary": "Students",
            "location": "Prishtina",
            "needs_or_motivations": ["Affordable fitness"],
        },
        "offer": None,
        "value_proposition": "Flexible gym access for students.",
        "positioning": "An accessible local gym for students.",
        "key_message": "Build a healthy routine on a student budget.",
        "strategy": "Reach students with useful short-form content.",
        "channels": [
            {
                "name": "Instagram",
                "purpose": "Reach local students",
                "reason": "The confirmed audience uses visual social content",
            }
        ],
        "content_direction": [
            {"idea": "Student workout tips", "purpose": "Build relevance and trust"}
        ],
        "budget_allocation": {
            "total": 300,
            "currency": "EUR",
            "items": [
                {
                    "channel": "Instagram",
                    "amount": 300,
                    "reason": "Fund the confirmed channel",
                }
            ],
        },
        "timeline": [
            {
                "period": "Weeks 1-2",
                "phase": "Launch",
                "objective": "Build awareness and trials",
                "activities": ["Publish student-focused content"],
            }
        ],
        "kpis": [{"name": "New student memberships", "purpose": "Measure acquisition"}],
        "assumptions_or_risks": ["Creative assets require client approval"],
        "next_steps": ["Confirm the content calendar"],
    }


@pytest.mark.asyncio
async def test_generator_uses_confirmed_brief_and_returns_validated_plan() -> None:
    brief = CampaignBrief(
        business="FitZone Gym",
        product_or_service="Gym membership",
        goal="Acquire student members",
        audience="Students",
        location="Prishtina",
        channels=["Instagram"],
        budget_amount=300,
        budget_currency="EUR",
        duration="2 weeks",
    )
    llm = FakeLLM(json.dumps(_plan_data()))

    plan = await CampaignPlanGenerator(llm).generate(brief)

    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_name == "Student Fitness Boost"
    assert plan.budget_allocation is not None
    assert plan.budget_allocation.total == 300
    assert len(llm.requests) == 1
    request = llm.requests[0]
    assert request.response_format == "json"
    context = json.loads(request.messages[1].content)
    assert context["confirmed_campaign_brief"] == brief.model_dump(mode="json")
    prompt = request.messages[0].content
    assert "marketing strategist" in prompt
    assert "Never contradict" in prompt
    assert "budget_allocation.total must equal" in prompt
    assert "budget_currency" in prompt
    assert "duration" in prompt
    assert "assumptions_or_risks" in prompt


@pytest.mark.asyncio
async def test_generator_repair_context_keeps_same_brief_and_safe_issue_codes() -> None:
    brief = CampaignBrief(
        business="FitZone Gym",
        goal="Acquire student members",
        audience="Students",
        budget_amount=300,
        budget_currency="EUR",
    )
    llm = FakeLLM(json.dumps(_plan_data()))

    await CampaignPlanGenerator(llm).generate(
        brief,
        repair_issues=("budget.total_mismatch",),
    )

    context = json.loads(llm.requests[0].messages[1].content)
    assert context["confirmed_campaign_brief"] == brief.model_dump(mode="json")
    assert context["previous_validation_issues"] == ["budget.total_mismatch"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        json.dumps({"campaign_name": "Incomplete"}),
    ],
)
async def test_generator_rejects_malformed_or_schema_invalid_output(response: str) -> None:
    with pytest.raises(
        ProviderResponseError,
        match="campaign plan generation returned invalid structured output",
    ):
        await CampaignPlanGenerator(FakeLLM(response)).generate(
            CampaignBrief(business="Acme", goal="Grow", audience="Retailers")
        )


@pytest.mark.asyncio
async def test_ready_campaign_generates_persists_and_reaches_plan_ready() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = CampaignBrief(
        business="FitZone Gym",
        goal="Acquire student members",
        audience="Students",
        location="Prishtina",
        channels=["Instagram"],
        budget_amount=300,
        budget_currency="EUR",
    )
    plan = CampaignPlan.model_validate(_plan_data())
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
    repository.save_plan = AsyncMock(return_value=plan)
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(return_value=plan)

    result = await CampaignService(repository).generate_plan(
        campaign_id=campaign_id,
        scope=scope,
        generator=generator,
    )

    assert result == plan
    generator.generate.assert_awaited_once_with(brief, repair_issues=())
    assert repository.update_status.await_args_list[0].kwargs == {
        "campaign_id": campaign_id,
        "scope": scope,
        "status": CampaignStatus.GENERATING,
    }
    assert repository.update_status.await_args_list[1].kwargs["status"] is (
        CampaignStatus.PLAN_READY
    )
    repository.save_plan.assert_awaited_once_with(
        campaign_id=campaign_id,
        scope=scope,
        plan=plan,
    )


@pytest.mark.asyncio
async def test_non_ready_campaign_cannot_initiate_generation() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = CampaignBrief(business="Acme")
    repository = Mock(spec=CampaignRepository)
    repository.get_brief = AsyncMock(return_value=brief)
    repository.get_campaign = AsyncMock(
        return_value=_campaign(campaign_id=campaign_id, status=CampaignStatus.BRIEFING)
    )
    repository.update_status = AsyncMock()
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock()

    with pytest.raises(InvalidCampaignTransitionError):
        await CampaignService(repository).generate_plan(
            campaign_id=campaign_id,
            scope=scope,
            generator=generator,
        )

    generator.generate.assert_not_awaited()
    repository.update_status.assert_not_awaited()
    repository.save_plan.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_restores_ready_and_preserves_brief() -> None:
    campaign_id = uuid4()
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    brief = CampaignBrief(
        business="Acme",
        goal="Grow",
        audience="Retailers",
        budget_amount=300,
        budget_currency="EUR",
    )
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
            _campaign(campaign_id=campaign_id, status=CampaignStatus.READY),
        ]
    )
    generator = Mock(spec=CampaignPlanGenerator)
    generator.generate = AsyncMock(side_effect=ProviderError("provider unavailable"))

    with pytest.raises(ProviderError, match="provider unavailable"):
        await CampaignService(repository).generate_plan(
            campaign_id=campaign_id,
            scope=scope,
            generator=generator,
        )

    assert repository.update_status.await_args_list[0].kwargs["status"] is (
        CampaignStatus.GENERATING
    )
    assert repository.update_status.await_args_list[1].kwargs["status"] is CampaignStatus.READY
    assert await repository.get_brief(campaign_id=campaign_id, scope=scope) == brief
    generator.generate.assert_awaited_once_with(brief, repair_issues=())
    repository.update_brief.assert_not_called()
    repository.save_plan.assert_not_called()
