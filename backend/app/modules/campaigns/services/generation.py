import json

from pydantic import ValidationError

from app.integrations.llm import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services.structured_output import parse_json_object


class CampaignPlanGenerator:
    """Turn one confirmed Campaign Brief into a validated, unpersisted plan."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def generate(self, brief: CampaignBrief) -> CampaignPlan:
        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_generation_prompt()),
                    LLMMessage(role="user", content=_brief_context(brief)),
                ),
                temperature=0.2,
                response_format="json",
            )
        )
        try:
            return CampaignPlan.model_validate(parse_json_object(response.text))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise ProviderResponseError(
                "campaign plan generation returned invalid structured output"
            ) from exc


def _brief_context(brief: CampaignBrief) -> str:
    return json.dumps(
        {"confirmed_campaign_brief": brief.model_dump(mode="json")},
        ensure_ascii=False,
        sort_keys=True,
    )


def _generation_prompt() -> str:
    schema = json.dumps(CampaignPlan.model_json_schema(), ensure_ascii=False, sort_keys=True)
    return f"""You are Promotiva's marketing strategist. Create one practical Campaign Plan.
Return exactly one JSON object and no prose or markdown outside it. The object must satisfy
this CampaignPlan JSON Schema:
{schema}

Source-of-truth rules:
- confirmed_campaign_brief contains facts confirmed by the client. Never contradict them.
- Never invent an unknown business fact or present a recommendation as client-confirmed.
- Strategic recommendations are allowed. Record material assumptions or recommendations
  caused by missing information in assumptions_or_risks.
- Keep offer null when no offer is confirmed unless a proposed offer is clearly identified as
  a recommendation in assumptions_or_risks.

Quality rules:
- Align objective, strategy, key message and KPIs with the confirmed campaign goal.
- Use the confirmed target audience and location meaningfully.
- Preserve confirmed channels. Explain each channel's strategic purpose and reason. Additional
  channel recommendations must be identified in assumptions_or_risks.
- If budget_amount is confirmed, budget_allocation.total must equal it, use the confirmed
  budget_currency, and item amounts must sum to the total. Do not invent a confirmed budget.
- If duration is confirmed, the timeline periods and phases must reflect that duration. Do not
  present an invented duration as confirmed.
- Provide actionable content direction, relevant KPIs and practical next steps.
- Ensure every required schema field is present and use null only where the schema permits it.
"""


__all__ = ["CampaignPlanGenerator"]
