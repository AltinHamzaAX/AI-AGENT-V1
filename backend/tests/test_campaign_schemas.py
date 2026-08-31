from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan, Objective, TargetAudience


def _campaign_plan() -> dict[str, object]:
    return {
        "campaign_name": "Student Fitness Boost",
        "executive_summary": "A focused two-week student acquisition campaign.",
        "objective": {
            "primary": "Acquire new customers",
            "secondary": "Increase student memberships",
        },
        "target_audience": {
            "primary": "Students aged 18-25",
            "location": "Prishtina",
            "needs_or_motivations": ["Affordable access", "Flexible opening hours"],
        },
        "offer": "50% off the first month",
        "value_proposition": "Modern equipment with flexible opening hours.",
        "positioning": "The accessible gym for active students.",
        "key_message": "Build your routine without stretching your budget.",
        "strategy": "Use short-form video and student-focused proof points.",
        "channels": [
            {
                "name": "Instagram",
                "purpose": "Reach local students",
                "reason": "The audience uses visual short-form content.",
            }
        ],
        "content_direction": [
            {
                "idea": "Student routine stories",
                "purpose": "Show how membership fits student schedules.",
            }
        ],
        "budget_allocation": {
            "total": 200,
            "currency": "EUR",
            "items": [
                {
                    "channel": "Instagram",
                    "amount": 200,
                    "reason": "Concentrate the available paid budget.",
                }
            ],
        },
        "timeline": [
            {
                "period": "Week 1",
                "phase": "Launch",
                "objective": "Build awareness",
                "activities": ["Publish launch video", "Start paid promotion"],
            }
        ],
        "kpis": [{"name": "Membership inquiries", "purpose": "Measure intent"}],
        "assumptions_or_risks": ["Student demand may vary by exam schedule."],
        "next_steps": ["Confirm creative assets."],
    }


def test_campaign_brief_supports_partial_data() -> None:
    assert CampaignBrief() == CampaignBrief(
        business=None,
        product_or_service=None,
        goal=None,
        audience=None,
        location=None,
        offer=None,
        value_proposition=None,
        channels=None,
        budget_amount=None,
        budget_currency=None,
        duration=None,
        brand_tone=None,
        constraints=None,
    )
    brief = CampaignBrief(business="  FitZone Gym  ", goal="Acquire customers")

    assert brief.business == "FitZone Gym"
    assert brief.offer is None
    assert brief.channels is None


def test_campaign_status_defines_only_ticket_one_states() -> None:
    assert {status.value for status in CampaignStatus} == {
        "BRIEFING",
        "READY",
        "GENERATING",
        "PLAN_READY",
    }


def test_campaign_plan_validates_the_approved_nested_structure() -> None:
    plan = CampaignPlan.model_validate(_campaign_plan())

    assert plan.objective.primary == "Acquire new customers"
    assert plan.objective.secondary == "Increase student memberships"
    assert plan.target_audience.primary == "Students aged 18-25"
    assert plan.target_audience.location == "Prishtina"
    assert plan.target_audience.needs_or_motivations == [
        "Affordable access",
        "Flexible opening hours",
    ]
    assert plan.budget_allocation is not None
    assert plan.budget_allocation.total == Decimal("200")
    assert plan.channels[0].name == "Instagram"


def test_objective_supports_nullable_secondary_and_rejects_invalid_structure() -> None:
    objective = Objective(primary="  Acquire new customers  ", secondary=None)

    assert objective.primary == "Acquire new customers"
    assert objective.secondary is None

    with pytest.raises(ValidationError):
        Objective(primary="   ")

    with pytest.raises(ValidationError):
        Objective.model_validate({"primary": "Acquire customers", "unknown": "value"})


def test_target_audience_validates_explicit_fields() -> None:
    audience = TargetAudience(
        primary="Students",
        location=None,
        needs_or_motivations=["Affordable access", "Flexible hours"],
    )

    assert audience.location is None
    assert audience.needs_or_motivations == ["Affordable access", "Flexible hours"]

    with pytest.raises(ValidationError):
        TargetAudience(primary="   ", needs_or_motivations=[])

    with pytest.raises(ValidationError):
        TargetAudience.model_validate({"primary": "Students"})

    with pytest.raises(ValidationError):
        TargetAudience.model_validate(
            {"primary": "Students", "location": None, "needs_or_motivations": "Affordable"}
        )

    with pytest.raises(ValidationError):
        TargetAudience(
            primary="Students",
            location=None,
            needs_or_motivations=["   "],
        )


@pytest.mark.parametrize(
    ("update", "error"),
    [
        ({"objective": ["Acquire customers"]}, "objective"),
        ({"channels": [{"name": "Instagram"}]}, "purpose"),
        ({"budget_allocation": {"total": -1, "currency": "EUR", "items": []}}, "total"),
        ({"unexpected": "field"}, "Extra inputs are not permitted"),
    ],
)
def test_campaign_plan_rejects_structurally_invalid_data(
    update: dict[str, object],
    error: str,
) -> None:
    payload = _campaign_plan()
    payload.update(update)

    with pytest.raises(ValidationError, match=error):
        CampaignPlan.model_validate(payload)


def test_campaign_brief_rejects_invalid_supplied_values() -> None:
    with pytest.raises(ValidationError):
        CampaignBrief(channels=["Instagram", "   "])

    with pytest.raises(ValidationError):
        CampaignBrief(budget_amount=-0.01)
