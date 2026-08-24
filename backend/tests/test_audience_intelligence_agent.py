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
from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis
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
    AudienceIntelligenceStageHandler,
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
            model="audience-intelligence-test",
            input_tokens=140,
            output_tokens=100,
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
        audience="Diaspora arriving in Kosovo",
        market="Kosovo",
        location="Prishtina airport",
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


def _brand_product(contract: PostSemanticContract) -> tuple[BrandAnalysis, ProductAnalysis]:
    brand = BrandAnalysis(
        company=contract.company,
        name=contract.brand,
        identity_summary="A dependable airport mobility brand.",
        personality_traits=["dependable", "clear"],
        verified_facts={},
        constraints=list(contract.constraints),
        contract_fingerprint=contract.fingerprint,
    )
    product = ProductAnalysis(
        name=contract.product,
        primary_entity=contract.primary_entity,
        offer=contract.offer,
        feature_benefit_value=[
            {
                "source_fact": "pickup availability",
                "feature": "24/7 airport pickup",
                "benefit": "No waiting after landing",
                "customer_value": "Convenience and certainty",
            }
        ],
        usp_candidates=[
            {
                "text": "Round-the-clock airport pickup",
                "source_facts": ["pickup availability"],
            }
        ],
        verified_facts=dict(contract.required_facts),
        forbidden_claims=list(contract.forbidden_claims),
        constraints=list(contract.constraints),
        required_assets=list(contract.required_assets),
        contract_fingerprint=contract.fingerprint,
    )
    return brand, product


def _insight(text: str, *basis: str, confidence: str = "medium") -> dict:
    return {"insight": text, "basis": list(basis), "confidence": confidence}


def _response(**overrides) -> str:
    audience_basis = "semantic_contract.audience"
    pickup_basis = "product.feature_benefit_value.pickup availability"
    value = {
        "segments": [
            {
                "name": "Arrival-focused diaspora travelers",
                "description": "Diaspora travelers who need transport immediately after landing.",
                "basis": [audience_basis, pickup_basis],
                "confidence": "medium",
            },
            {
                "name": "Convenience-first diaspora family visitors",
                "description": (
                    "Diaspora visitors prioritizing a predictable airport-to-destination journey."
                ),
                "basis": [audience_basis, "semantic_contract.location"],
                "confidence": "low",
            },
        ],
        "target": {
            "segment": "Arrival-focused diaspora travelers",
            "rationale": "The declared audience and airport pickup fact directly fit this segment.",
            "basis": [audience_basis, pickup_basis],
            "confidence": "medium",
        },
        "needs": [
            _insight("Immediate access to transport after landing.", pickup_basis)
        ],
        "desires": [
            _insight("A smooth transition from arrival to onward travel.", pickup_basis)
        ],
        "pain_points": [
            _insight("Waiting for transport after a flight.", pickup_basis)
        ],
        "objections": [
            _insight(
                "Uncertainty about whether the vehicle will be ready.",
                pickup_basis,
                confidence="low",
            )
        ],
        "motivation": [
            _insight("Start the visit without avoidable delays.", pickup_basis)
        ],
        "purchase_intent": {
            "level": "medium",
            "rationale": "The audience has a time-sensitive transport use case.",
            "basis": [audience_basis, pickup_basis],
            "confidence": "medium",
        },
        "trust_triggers": [
            _insight("Clear confirmation of 24/7 pickup availability.", pickup_basis)
        ],
        "situations": [
            _insight("Arriving at Prishtina airport after a flight.", "semantic_contract.location")
        ],
        "customer_tension": {
            "current_state": "The traveler has landed and still needs dependable transport.",
            "desired_state": "The car is ready immediately after landing.",
            "tension": "They do not want to wait or face uncertainty after arrival.",
            "basis": [audience_basis, pickup_basis],
            "confidence": "medium",
        },
    }
    value.update(overrides)
    return json.dumps(value)


def _context(contract: PostSemanticContract | None = None) -> SupervisorStageContext:
    semantic_contract = contract or _contract()
    brand, product = _brand_product(semantic_contract)
    state = empty_workflow_state()
    state[PostWorkflowSection.SEMANTIC_CONTRACT.value] = semantic_contract.to_dict()
    state[PostWorkflowSection.BRAND.value] = brand.model_dump(mode="json")
    state[PostWorkflowSection.PRODUCT.value] = product.model_dump(mode="json")
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=5,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_audience_intelligence_builds_grounded_customer_tension() -> None:
    contract = _contract()
    llm = _SequenceLLM(_response())
    recorder = InMemoryExecutionTraceRecorder()
    handler = AudienceIntelligenceStageHandler(
        _providers(llm),
        trace_recorder=recorder,
    )

    result = await handler.execute(_context(contract))

    assert set(result.outputs) == {PostWorkflowSection.AUDIENCE}
    audience = result.outputs[PostWorkflowSection.AUDIENCE]
    assert {segment["parent_audience"] for segment in audience["segments"]} == {
        contract.audience
    }
    assert audience["target"]["segment"] == "Arrival-focused diaspora travelers"
    assert audience["customer_tension"] == {
        "current_state": "The traveler has landed and still needs dependable transport.",
        "desired_state": "The car is ready immediately after landing.",
        "tension": "They do not want to wait or face uncertainty after arrival.",
        "basis": [
            "semantic_contract.audience",
            "product.feature_benefit_value.pickup availability",
        ],
        "confidence": "medium",
    }
    assert audience["context"]["declared_audience"] == contract.audience
    assert audience["context"]["market"] == contract.market
    assert audience["context"]["location"] == contract.location
    assert audience["context"]["platform"] == contract.platform
    assert audience["contract_fingerprint"] == contract.fingerprint
    assert audience["limitations"] == [
        "Audience insights are reasoned hypotheses until the External Research stage "
        "validates them."
    ]
    assert "marketing_strategy" not in audience
    assert "positioning" not in audience
    assert llm.requests[0].temperature == 0
    assert llm.requests[0].response_format == "json"
    provider_payload = json.loads(llm.requests[0].messages[1].content)
    assert provider_payload["analysis_language"] == "English"
    assert "goal" not in provider_payload["semantic_contract"]
    assert "offer" not in provider_payload["semantic_contract"]
    assert "personality_traits" not in provider_payload["brand"]
    assert "forbidden_claims" not in provider_payload["semantic_contract"]
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.PROVIDER,
        ExecutionRunKind.AGENT,
    ]
    assert all(trace.status is ExecutionRunStatus.SUCCEEDED for trace in recorder.traces)


@pytest.mark.asyncio
async def test_audience_intelligence_rejects_unknown_evidence_basis() -> None:
    response = json.loads(_response())
    response["needs"][0]["basis"] = ["external_research.fake_statistic"]
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError) as failure:
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert failure.value.attempts == 2
    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_target_must_be_one_of_the_declared_segments() -> None:
    response = json.loads(_response())
    response["target"]["segment"] = "Unrelated luxury shoppers"
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_segments_cannot_expand_beyond_declared_audience() -> None:
    response = json.loads(_response())
    response["segments"][1] = {
        "name": "Local residents",
        "description": "Residents looking for everyday transportation.",
        "basis": ["semantic_contract.market"],
        "confidence": "low",
    }
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_audience_intelligence_rejects_forbidden_claim() -> None:
    response = json.loads(_response())
    response["trust_triggers"][0]["insight"] = "Cheapest rental in Kosovo"
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_audience_intelligence_rejects_strategy_scope_drift() -> None:
    response = json.loads(_response())
    response["positioning"] = "Fastest arrival solution"
    response["copy"] = "Land. Drive. Relax."
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_high_confidence_requires_later_external_research() -> None:
    response = json.loads(_response())
    response["needs"][0]["confidence"] = "high"
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_unverified_behavior_and_affordability_assumptions_are_rejected() -> None:
    response = json.loads(_response())
    response["desires"][0]["insight"] = "Affordable pricing aligned with their budget."
    response["segments"][0]["name"] = "Frequent diaspora visitors"
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_unverified_cost_objection_is_rejected() -> None:
    response = json.loads(_response())
    response["objections"][0]["insight"] = "Concern about potential high costs."
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_unverified_brand_reputation_is_rejected() -> None:
    response = json.loads(_response())
    response["trust_triggers"][0]["insight"] = "Trust based on the brand's reputation."
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_unverified_peak_time_context_is_rejected() -> None:
    response = json.loads(_response())
    response["situations"][0]["insight"] = "Arriving during peak times."
    llm = _SequenceLLM(json.dumps(response))

    with pytest.raises(InvocationFailedError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert len(llm.requests) == 4


@pytest.mark.asyncio
async def test_guardrail_feedback_repairs_invalid_structured_output() -> None:
    invalid = json.loads(_response())
    invalid["situations"][0]["insight"] = "Arriving during peak times."
    llm = _SequenceLLM(json.dumps(invalid), _response())

    result = await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    assert set(result.outputs) == {PostWorkflowSection.AUDIENCE}
    assert len(llm.requests) == 2
    assert len(llm.requests[1].messages) == 4
    assert "unsupported audience assumption" in llm.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_single_supported_segment_is_kept_with_research_limitation() -> None:
    response = json.loads(_response())
    response["segments"] = [response["segments"][0]]
    llm = _SequenceLLM(json.dumps(response))

    result = await AudienceIntelligenceStageHandler(_providers(llm)).execute(_context())

    audience = result.outputs[PostWorkflowSection.AUDIENCE]
    assert len(audience["segments"]) == 1
    assert audience["limitations"][-1] == (
        "Only one evidence-supported segment was identified; External Research should test "
        "additional segmentation."
    )


@pytest.mark.asyncio
async def test_tampered_brand_product_state_fails_before_provider_call() -> None:
    context = _context()
    context.workflow_state[PostWorkflowSection.PRODUCT.value]["name"] = "BMW"
    llm = _SequenceLLM(_response())

    with pytest.raises(ValueError, match="protected product facts"):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(context)

    assert llm.requests == []


@pytest.mark.asyncio
async def test_missing_brand_or_product_state_fails_before_provider_call() -> None:
    context = _context()
    context.workflow_state[PostWorkflowSection.BRAND.value] = {}
    llm = _SequenceLLM(_response())

    with pytest.raises(ValueError):
        await AudienceIntelligenceStageHandler(_providers(llm)).execute(context)

    assert llm.requests == []
