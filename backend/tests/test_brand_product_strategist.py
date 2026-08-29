import json
from uuid import uuid4

import pytest

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.exceptions import InvocationFailedError
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    InMemoryExecutionTraceRecorder,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import SupervisorAction
from app.modules.posts.orchestration import (
    BrandProductStageHandler,
    SupervisorStageContext,
)
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderBundle


class _SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=self._responses[index],
            provider="test",
            model="brand-product-test",
            input_tokens=100,
            output_tokens=60,
        )


def _providers(llm: _SequenceLLM) -> ProviderBundle:
    return ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={
            "llm": "test",
            "vision": "mock",
            "image": "mock",
            "embedding": "mock",
            "research": "mock",
            "storage": "mock",
        },
    )


def _contract() -> PostSemanticContract:
    return PostSemanticContract.create(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Airport car rental",
        primary_entity="Airport car rental",
        goal="Drive bookings",
        audience="Travelers arriving in Prishtina",
        market="Kosovo",
        location="Prishtina",
        offer="From EUR 35/day",
        cta_intent="Book now",
        platform="Instagram",
        language="Albanian",
        required_facts={
            "pickup availability": "24/7 airport pickup",
            "vehicle class": "compact automatic car",
        },
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=[uuid4()],
        constraints=["Do not replace the product or logo"],
    )


def _response(**overrides) -> str:
    value = {
        "identity_summary": "A dependable mobility brand grounded in verified service facts.",
        "personality_traits": ["dependable", "clear"],
        "brand_fact_keys": [],
        "product_fact_keys": ["pickup availability", "vehicle class"],
        "feature_benefit_value": [
            {
                "source_fact": "pickup availability",
                "feature": "24/7 airport pickup",
                "benefit": "No waiting after landing",
                "customer_value": "Convenience and certainty",
            }
        ],
        "usp_candidates": [
            {
                "text": "Round-the-clock airport pickup",
                "source_facts": ["pickup availability"],
            }
        ],
    }
    value.update(overrides)
    return json.dumps(value)


def _context(contract: PostSemanticContract | None = None) -> SupervisorStageContext:
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = (contract or _contract()).to_dict()
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=3,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_brand_product_maps_feature_to_benefit_and_customer_value() -> None:
    contract = _contract()
    llm = _SequenceLLM(_response())
    recorder = InMemoryExecutionTraceRecorder()
    handler = BrandProductStageHandler(_providers(llm), trace_recorder=recorder)

    result = await handler.execute(_context(contract))

    assert set(result.outputs) == {
        PostWorkflowSection.BRAND,
        PostWorkflowSection.PRODUCT,
    }
    brand = result.outputs[PostWorkflowSection.BRAND]
    product = result.outputs[PostWorkflowSection.PRODUCT]
    assert brand["company"] == contract.company
    assert brand["name"] == contract.brand
    assert product["name"] == contract.product
    assert product["primary_entity"] == contract.primary_entity
    assert product["offer"] == contract.offer
    assert brand["verified_facts"] == {}
    assert product["verified_facts"] == dict(contract.required_facts)
    assert product["required_assets"] == [str(contract.required_assets[0])]
    assert product["feature_benefit_value"] == [
        {
            "source_fact": "pickup availability",
            "feature": "24/7 airport pickup",
            "benefit": "No waiting after landing",
            "customer_value": "Convenience and certainty",
        }
    ]
    assert "marketing_strategy" not in product
    assert "copy" not in product
    assert llm.requests[0].temperature == 0
    assert llm.requests[0].response_format == "json"
    assert "analytical prose in clear, concise English" in llm.requests[0].messages[0].content
    assert "do not translate" in llm.requests[0].messages[0].content
    provider_payload = json.loads(llm.requests[0].messages[1].content)
    assert provider_payload["analysis_language"] == "English"
    assert "language" not in provider_payload
    assert "audience" not in provider_payload
    assert "goal" not in provider_payload
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.PROVIDER,
        ExecutionRunKind.AGENT,
    ]
    assert all(trace.status is ExecutionRunStatus.SUCCEEDED for trace in recorder.traces)


@pytest.mark.asyncio
async def test_product_carries_every_fact_referenced_by_its_promises() -> None:
    result = await BrandProductStageHandler(
        _providers(
            _SequenceLLM(
                _response(
                    brand_fact_keys=["pickup availability"],
                    product_fact_keys=["vehicle class"],
                )
            )
        )
    ).execute(_context())

    product = result.outputs[PostWorkflowSection.PRODUCT]
    assert product["verified_facts"] == {
        "vehicle class": "compact automatic car",
        "pickup availability": "24/7 airport pickup",
    }


@pytest.mark.asyncio
async def test_brand_product_rejects_an_unsupported_feature() -> None:
    llm = _SequenceLLM(
        _response(
            feature_benefit_value=[
                {
                    "source_fact": "free insurance",
                    "feature": "Free full insurance",
                    "benefit": "No risk",
                    "customer_value": "Peace of mind",
                }
            ]
        )
    )

    with pytest.raises(InvocationFailedError) as failure:
        await BrandProductStageHandler(_providers(llm)).execute(_context())

    assert failure.value.attempts == 2
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_brand_product_rejects_forbidden_claims() -> None:
    llm = _SequenceLLM(
        _response(
            usp_candidates=[
                {
                    "text": "The cheapest rental in Kosovo",
                    "source_facts": ["pickup availability"],
                }
            ]
        )
    )

    with pytest.raises(InvocationFailedError):
        await BrandProductStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_brand_product_requires_all_facts_to_be_classified() -> None:
    llm = _SequenceLLM(_response(product_fact_keys=["pickup availability"]))

    with pytest.raises(InvocationFailedError):
        await BrandProductStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_usp_candidate_must_cite_a_verified_fact() -> None:
    llm = _SequenceLLM(
        _response(
            usp_candidates=[
                {
                    "text": "Premium concierge service",
                    "source_facts": ["concierge service"],
                }
            ]
        )
    )

    with pytest.raises(InvocationFailedError):
        await BrandProductStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_provider_cannot_replace_product_or_add_strategy_fields() -> None:
    response = json.loads(_response())
    response["product"] = "BMW"
    response["marketing_strategy"] = {"positioning": "luxury"}
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await BrandProductStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_brand_product_requires_a_valid_semantic_contract() -> None:
    context = _context()
    context.workflow_state[PostWorkflowSection.SEMANTIC_CONTRACT.value]["fingerprint"] = "0" * 64
    llm = _SequenceLLM(_response())

    with pytest.raises(ValueError, match="fingerprint"):
        await BrandProductStageHandler(_providers(llm)).execute(context)

    assert llm.requests == []
