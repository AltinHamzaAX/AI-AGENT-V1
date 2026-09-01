from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


ShortText = Annotated[str, Field(min_length=1, max_length=500)]
LongText = Annotated[str, Field(min_length=1, max_length=20_000)]
Money = Annotated[Decimal, Field(ge=0)]


class CampaignSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignBrief(CampaignSchema):
    """Campaign facts collected incrementally during briefing."""

    business: ShortText | None = None
    product_or_service: ShortText | None = None
    goal: LongText | None = None
    audience: LongText | None = None
    location: ShortText | None = None
    offer: LongText | None = None
    value_proposition: LongText | None = None
    channels: list[ShortText] | None = Field(default=None, max_length=100)
    budget_amount: Money | None = None
    budget_currency: ShortText | None = None
    duration: ShortText | None = None
    brand_tone: LongText | None = None
    constraints: list[LongText] | None = Field(default=None, max_length=100)


class CampaignConversationResult(CampaignSchema):
    """Validated reply and facts proposed by one Campaign conversation turn."""

    reply: LongText
    extracted_fields: CampaignBrief


class Objective(CampaignSchema):
    primary: LongText
    secondary: LongText | None = None


class TargetAudience(CampaignSchema):
    primary: LongText
    location: ShortText | None = None
    needs_or_motivations: list[LongText] = Field(max_length=100)


class ChannelStrategy(CampaignSchema):
    name: ShortText
    purpose: LongText
    reason: LongText


class ContentDirection(CampaignSchema):
    idea: LongText
    purpose: LongText


class BudgetItem(CampaignSchema):
    channel: ShortText
    amount: Money
    reason: LongText


class BudgetAllocation(CampaignSchema):
    total: Money
    currency: ShortText
    items: list[BudgetItem] = Field(max_length=100)


class TimelinePhase(CampaignSchema):
    period: ShortText
    phase: ShortText
    objective: LongText
    activities: list[LongText] = Field(max_length=100)


class KPI(CampaignSchema):
    name: ShortText
    purpose: LongText


class CampaignPlan(CampaignSchema):
    campaign_name: ShortText
    executive_summary: LongText
    objective: Objective
    target_audience: TargetAudience
    offer: LongText | None
    value_proposition: LongText
    positioning: LongText
    key_message: LongText
    strategy: LongText
    channels: list[ChannelStrategy] = Field(max_length=100)
    content_direction: list[ContentDirection] = Field(max_length=100)
    budget_allocation: BudgetAllocation | None
    timeline: list[TimelinePhase] = Field(max_length=100)
    kpis: list[KPI] = Field(max_length=100)
    assumptions_or_risks: list[LongText] = Field(max_length=100)
    next_steps: list[LongText] = Field(max_length=100)


__all__ = [
    "BudgetAllocation",
    "BudgetItem",
    "CampaignBrief",
    "CampaignConversationResult",
    "CampaignPlan",
    "ChannelStrategy",
    "ContentDirection",
    "KPI",
    "Objective",
    "TargetAudience",
    "TimelinePhase",
]
