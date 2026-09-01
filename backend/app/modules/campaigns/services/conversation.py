import json

from pydantic import ValidationError

from app.integrations.llm import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderResponseError,
)
from app.modules.campaigns.schemas import CampaignBrief, CampaignConversationResult
from app.modules.campaigns.services.structured_output import parse_json_object


class CampaignConversationExtractor:
    """Produce one conversational reply and validated, unpersisted Brief facts."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def respond(
        self,
        *,
        message: str,
        current_brief: CampaignBrief,
    ) -> CampaignConversationResult:
        if not message.strip():
            raise ValueError("Campaign conversation message cannot be empty")
        context = json.dumps(
            {
                "current_brief": current_brief.model_dump(mode="json"),
                "latest_user_message": message,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        response = await self._llm.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content=_system_prompt()),
                    LLMMessage(role="user", content=context),
                ),
                temperature=0.2,
                response_format="json",
            )
        )
        try:
            return CampaignConversationResult.model_validate(
                parse_json_object(response.text)
            )
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise ProviderResponseError(
                "campaign conversation returned invalid structured output"
            ) from exc


def _system_prompt() -> str:
    fields = {name: None for name in CampaignBrief.model_fields}
    answer_shape = json.dumps(
        {"reply": "A natural reply", "extracted_fields": fields},
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"""You are Promotiva's Campaign planning assistant. Process one client message.
Return exactly one JSON object and no prose or markdown outside it, shaped as:
{answer_shape}

Reply rules:
- Write reply in the language used by latest_user_message.
- Be natural, concise, warm and focused on planning the campaign.
- Acknowledge useful new information and ask at most one relevant follow-up question.
- Use current_brief to avoid asking for information already known.
- Do not decide or mention Campaign readiness, state transitions, persistence or internals.

Extraction rules:
- extracted_fields describes only facts explicitly stated or unambiguously supplied in
  latest_user_message. Never copy unchanged facts from current_brief into extracted_fields.
- A clear correction belongs in extracted_fields even when it differs from current_brief.
- Never invent facts, fill gaps, infer business claims, or turn your own recommendations into
  confirmed facts. Recommendations may appear only in reply unless the client confirmed them.
- Leave every unknown or unchanged field null. Do not use empty strings as unknown values.
- channels and constraints are arrays of non-empty strings or null. budget_amount is a
  non-negative number or null. Every other extracted field is a non-empty string or null.
- Preserve the client's wording and do not translate extracted facts.

The current_brief is confirmed context, not a source of new extracted fields. The
latest_user_message is the only source for this turn's extracted_fields."""


__all__ = ["CampaignConversationExtractor"]
