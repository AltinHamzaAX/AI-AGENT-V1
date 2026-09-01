import json

import pytest

from app.integrations.llm import LLMRequest, LLMResponse, ProviderError, ProviderResponseError
from app.modules.campaigns.schemas import CampaignBrief
from app.modules.campaigns.services import CampaignConversationExtractor


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self.response, provider="fake", model="fake-model")


def _output(*, reply: str, extracted_fields: dict) -> str:
    return json.dumps(
        {"reply": reply, "extracted_fields": extracted_fields},
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_conversation_returns_natural_reply_and_multiple_validated_fields() -> None:
    llm = FakeLLM(
        _output(
            reply="Great, who should this campaign reach?",
            extracted_fields={
                "business": "Northstar Coffee",
                "product_or_service": "cold brew",
                "goal": "increase subscriptions",
                "channels": ["Instagram", "TikTok"],
            },
        )
    )

    result = await CampaignConversationExtractor(llm).respond(
        message=(
            "Northstar Coffee sells cold brew. We want to increase subscriptions through "
            "Instagram and TikTok."
        ),
        current_brief=CampaignBrief(),
    )

    assert result.reply == "Great, who should this campaign reach?"
    assert result.extracted_fields.business == "Northstar Coffee"
    assert result.extracted_fields.product_or_service == "cold brew"
    assert result.extracted_fields.goal == "increase subscriptions"
    assert result.extracted_fields.channels == ["Instagram", "TikTok"]
    assert result.extracted_fields.audience is None


@pytest.mark.asyncio
async def test_current_brief_is_context_but_not_automatically_reextracted() -> None:
    brief = CampaignBrief(
        business="FitZone",
        product_or_service="gym membership",
        goal="gain new members",
    )
    llm = FakeLLM(
        _output(
            reply="Which audience would you most like to reach?",
            extracted_fields={},
        )
    )

    await CampaignConversationExtractor(llm).respond(
        message="That is all correct so far.",
        current_brief=brief,
    )

    request = llm.requests[0]
    payload = json.loads(request.messages[-1].content)
    assert payload["current_brief"] == brief.model_dump(mode="json")
    assert payload["latest_user_message"] == "That is all correct so far."
    assert "avoid asking for information already known" in request.messages[0].content
    assert request.response_format == "json"


@pytest.mark.asyncio
async def test_clear_correction_is_returned_without_persisting_or_merging_it() -> None:
    llm = FakeLLM(
        _output(
            reply="Understood, I will use a 500 EUR budget.",
            extracted_fields={"budget_amount": 500, "budget_currency": "EUR"},
        )
    )
    current = CampaignBrief(budget_amount=200, budget_currency="EUR")

    result = await CampaignConversationExtractor(llm).respond(
        message="Actually make the budget 500 EUR.",
        current_brief=current,
    )

    assert result.extracted_fields.budget_amount == 500
    assert result.extracted_fields.budget_currency == "EUR"
    assert current.budget_amount == 200


@pytest.mark.asyncio
async def test_recommendation_in_reply_is_not_a_confirmed_extracted_fact() -> None:
    llm = FakeLLM(
        _output(
            reply="TikTok could be worth considering. What budget do you have in mind?",
            extracted_fields={},
        )
    )

    result = await CampaignConversationExtractor(llm).respond(
        message="What channel would you recommend?",
        current_brief=CampaignBrief(product_or_service="running shoes"),
    )

    assert result.extracted_fields.channels is None
    assert "TikTok" in result.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        _output(reply="", extracted_fields={}),
        _output(reply="Reply", extracted_fields={"budget_amount": -1}),
        _output(reply="Reply", extracted_fields={"invented_field": "value"}),
    ],
)
async def test_malformed_or_invalid_structured_output_is_rejected(response: str) -> None:
    with pytest.raises(
        ProviderResponseError,
        match="campaign conversation returned invalid structured output",
    ):
        await CampaignConversationExtractor(FakeLLM(response)).respond(
            message="Help me plan a campaign.",
            current_brief=CampaignBrief(),
        )


@pytest.mark.asyncio
async def test_provider_failure_propagates_through_neutral_error_boundary() -> None:
    class FailingLLM:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise ProviderError("provider unavailable")

    with pytest.raises(ProviderError, match="provider unavailable"):
        await CampaignConversationExtractor(FailingLLM()).respond(
            message="Help me plan a campaign.",
            current_brief=CampaignBrief(),
        )
