import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from test_creative_director_agent import _CreativeLLM
from test_creative_director_agent import _input as _creative_input
from test_creative_director_agent import _run as _run_creative

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.copywriter import (
    COPYWRITER_DEFINITION,
    CopyDraft,
    CopywriterAgent,
    CopywriterInput,
)
from app.modules.posts.agents.framework import AgentExecutionContext
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import (
    DEFAULT_SUPERVISOR_PLAN,
    SupervisorAction,
    SupervisorStage,
)
from app.modules.posts.orchestration.copywriting import (
    CopywritingStageHandler,
    _agent_payload,
)
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import (
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ProviderResponseError,
)


def _copy_payload() -> dict[str, Any]:
    return {
        "headline": "Your journey starts at arrival",
        "subheadline": "A smoother handoff from the terminal to the road",
        "supporting_copy": (
            "Move from landing to exploring with a service shaped around confident travel."
        ),
        "offer_copy": "From EUR 35/day",
        "cta": "Book your drive",
        "caption": (
            "Arrival should feel like the beginning of the journey, not another pause. "
            "Step from the terminal into a drive built around confident movement."
        ),
        "hashtags": ["#TravelKosovo", "#AirportCarRental"],
    }


class _CopyLLM:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = responses or [_copy_payload()]
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(
            text=json.dumps(response),
            provider="test-llm",
            model="copywriter-test",
        )


async def _input() -> CopywriterInput:
    creative_input = await _creative_input()
    concept = await _run_creative(creative_input, _CreativeLLM())
    contract = PostSemanticContract.from_dict(creative_input.semantic_contract)
    return CopywriterInput(
        strategy=creative_input.marketing_strategy,
        concept=concept,
        brand_voice=creative_input.brand,
        platform=contract.platform,
        offer=contract.offer,
        semantic_contract=contract.to_dict(),
    )


def _context() -> AgentExecutionContext:
    return AgentExecutionContext(
        invocation=InvocationContext(
            correlation_id=uuid4(),
            post_id=uuid4(),
            generation_id=uuid4(),
        ),
        agent_name="copywriter",
        attempt=1,
    )


async def _run(payload: CopywriterInput, llm: _CopyLLM) -> CopyDraft:
    return await CopywriterAgent(llm).execute(payload, None, _context())


@pytest.mark.asyncio
async def test_copywriter_returns_all_ticket_fields_and_seven_quality_checks() -> None:
    payload = await _input()
    result = await _run(payload, _CopyLLM())

    assert result.headline == "Your journey starts at arrival"
    assert result.offer_copy == payload.offer
    assert result.cta == "Book your drive"
    assert result.hashtags == ["#TravelKosovo", "#AirportCarRental"]
    assert {check.dimension for check in result.quality.checks} == {
        "clarity",
        "tone",
        "length",
        "grammar",
        "claim_validity",
        "text_density",
        "mobile_readability",
    }
    assert result.quality.failures == []
    assert result.contract_fingerprint == payload.concept.contract_fingerprint


@pytest.mark.asyncio
async def test_only_the_winning_concept_reaches_the_copy_prompt() -> None:
    payload = await _input()
    llm = _CopyLLM()

    await _run(payload, llm)

    source = json.loads(llm.requests[0].messages[-1].content)["source"]
    assert source["winning_concept"]["id"] == payload.concept.winning_concept.candidate_id
    assert "rejected_concepts" not in source


@pytest.mark.asyncio
async def test_unsupported_numeric_claim_is_repaired_from_complete_output() -> None:
    payload = await _input()
    invalid = _copy_payload()
    invalid["supporting_copy"] = "Save 50% on every journey."

    result = await _run(payload, _CopyLLM([invalid, _copy_payload()]))

    assert "50%" not in result.supporting_copy


@pytest.mark.asyncio
async def test_repeated_unsupported_claim_fails_closed() -> None:
    payload = await _input()
    invalid = _copy_payload()
    invalid["headline"] = "The fastest guaranteed arrival"

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CopyLLM([invalid, invalid]))


@pytest.mark.asyncio
async def test_offer_must_be_preserved_exactly() -> None:
    payload = await _input()
    invalid = _copy_payload()
    invalid["offer_copy"] = "From EUR 25/day"

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CopyLLM([invalid, invalid]))


@pytest.mark.asyncio
async def test_mobile_readability_and_tone_are_enforced() -> None:
    payload = await _input()
    invalid = _copy_payload()
    invalid["headline"] = "THIS HEADLINE SHOUTS FAR TOO LOUDLY FOR A SMALL MOBILE POST TODAY"

    with pytest.raises(ProviderResponseError):
        await _run(payload, _CopyLLM([invalid, invalid]))


@pytest.mark.asyncio
async def test_contract_drift_is_rejected_before_provider_call() -> None:
    payload = await _input()
    drifted = payload.model_copy(
        update={
            "brand_voice": payload.brand_voice.model_copy(
                update={"contract_fingerprint": "0" * 64}
            )
        }
    )

    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        CopywriterInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_stage_writes_only_copy() -> None:
    payload = await _input()
    llm = _CopyLLM()
    providers = ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
    )
    state = {
        PostWorkflowSection.MARKETING_STRATEGY.value: payload.strategy.model_dump(mode="json"),
        PostWorkflowSection.CREATIVE_CONCEPT.value: payload.concept.model_dump(mode="json"),
        PostWorkflowSection.BRAND.value: payload.brand_voice.model_dump(mode="json"),
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
    }
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )

    result = await CopywritingStageHandler(providers).execute(context)

    assert set(result.outputs) == {PostWorkflowSection.COPY}
    assert result.outputs[PostWorkflowSection.COPY]["headline"] == _copy_payload()["headline"]
    assert _agent_payload(state)["platform"] == payload.platform


def test_supervisor_declares_all_copywriter_inputs() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.COPYWRITING)
    assert set(policy.required_sections) == {
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.MARKETING_STRATEGY,
        PostWorkflowSection.CREATIVE_CONCEPT,
        PostWorkflowSection.BRAND,
    }
    assert policy.output_sections == (PostWorkflowSection.COPY,)


def test_copywriter_has_no_tools_or_downstream_design_fields() -> None:
    assert COPYWRITER_DEFINITION.allowed_tools == frozenset()
    schema = CopyDraft.model_json_schema()
    serialized = json.dumps(schema)
    for forbidden in ("layout", "typography", "image_prompt", "logo_placement"):
        assert forbidden not in serialized


def test_hashtags_are_optional_normalized_and_deduplicated() -> None:
    payload = deepcopy(_copy_payload())
    payload["hashtags"] = ["TravelKosovo", "#travelkosovo", "Airport"]
    from app.modules.posts.agents.copywriter import CopywriterLLMOutput

    output = CopywriterLLMOutput.model_validate(payload)
    assert output.hashtags == ["#TravelKosovo", "#Airport"]
