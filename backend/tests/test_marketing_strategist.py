"""Ticket 21: the Marketing Strategist.

The agent decides; the application decides whether the decision is answerable.
Every rule pinned here exists because a strategy that cannot be traced back to
brief, product, audience or research is indistinguishable from a confident
guess, and every later stage would inherit it as fact.
"""

import json
from typing import Any
from uuid import uuid4

import pytest
from test_external_research_service import _audience, _ResearchProvider, _StructuredResearchLLM

from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis
from app.modules.posts.agents.client_understanding import ClientUnderstandingBrief
from app.modules.posts.agents.framework import AgentExecutionContext
from app.modules.posts.agents.marketing_strategist import (
    DECISION_PRINCIPLES,
    STRATEGY_DECISIONS,
    MarketingPrinciple,
    MarketingStrategistAgent,
    MarketingStrategy,
    MarketingStrategyInput,
    MessageFramework,
)
from app.modules.posts.agents.marketing_strategist.agent import (
    _resolved_basis_requirements,
    _system_prompt,
)
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage
from app.modules.posts.orchestration.marketing_strategy import _agent_payload
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderResponseError
from app.modules.posts.tools.research import (
    ExternalResearchService,
    InMemoryResearchCache,
    default_research_tools,
)
from app.modules.posts.tools.research.schemas import ExternalResearchInput

SEGMENT = "Arrival convenience seekers"
TARGET_BASIS = f"audience.segments.{SEGMENT}"


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


def _brand(contract: PostSemanticContract) -> BrandAnalysis:
    return BrandAnalysis(
        company=contract.company,
        name=contract.brand,
        identity_summary="A dependable airport mobility brand.",
        personality_traits=["dependable", "clear"],
        verified_facts={"pickup availability": "24/7 airport pickup"},
        constraints=list(contract.constraints),
        contract_fingerprint=contract.fingerprint,
    )


def _product(contract: PostSemanticContract) -> ProductAnalysis:
    return ProductAnalysis(
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
            {"text": "Round-the-clock airport pickup", "source_facts": ["pickup availability"]}
        ],
        verified_facts=dict(contract.required_facts),
        forbidden_claims=list(contract.forbidden_claims),
        constraints=list(contract.constraints),
        required_assets=list(contract.required_assets),
        contract_fingerprint=contract.fingerprint,
    )


def _brief(contract: PostSemanticContract) -> ClientUnderstandingBrief:
    return ClientUnderstandingBrief(
        business=contract.company,
        brand=contract.brand,
        product_service=contract.primary_entity,
        goal=contract.goal,
        audience=contract.audience,
        market=contract.market,
        location=contract.location,
        platform=contract.platform,
        language=contract.language,
        offer=contract.offer,
        cta_intent=contract.cta_intent,
        style_preferences=["clean", "trustworthy"],
        constraints=list(contract.constraints),
    )


async def _research(contract: PostSemanticContract):
    """A genuine research result, so the allowlist is the real one."""
    service = ExternalResearchService(
        default_research_tools(_ResearchProvider(), _StructuredResearchLLM()),
        cache=InMemoryResearchCache(),
        cache_ttl_seconds=600,
    )
    return await service.run(
        ExternalResearchInput(
            semantic_contract=contract.to_dict(),
            audience=_audience(contract),
        )
    )


async def _input(contract: PostSemanticContract | None = None) -> MarketingStrategyInput:
    source = contract or _contract()
    return MarketingStrategyInput(
        brief=_brief(source),
        semantic_contract=source.to_dict(),
        brand=_brand(source),
        product=_product(source),
        audience=_audience(source),
        research=await _research(source),
    )


def _decision(name: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "decision": f"Decided {name.replace('_', ' ')}.",
        "rationale": f"Because the evidence supports {name.replace('_', ' ')}.",
        "principle": DECISION_PRINCIPLES[name].value,
        "basis": ["semantic_contract.goal"],
    }
    body.update(overrides)
    return body


def _strategy_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {name: _decision(name) for name in STRATEGY_DECISIONS}
    payload["business_objective"] = _decision(
        "business_objective", basis=["semantic_contract.goal"]
    )
    payload["targeting"] = _decision(
        "targeting",
        decision=f"Target the {SEGMENT} segment first.",
        basis=[TARGET_BASIS],
    )
    payload["segmentation"] = _decision(
        "segmentation",
        basis=["semantic_contract.audience"],
    )
    payload["positioning"] = _decision(
        "positioning",
        basis=[TARGET_BASIS, "product.feature_benefit_value.pickup availability"],
    )
    payload["customer_insight"] = _decision(
        "customer_insight",
        basis=["audience.needs"],
    )
    payload["usp"] = _decision("usp", basis=["product.usp_candidates.1"])
    payload["customer_tension"] = _decision("customer_tension", basis=["audience.customer_tension"])
    payload["value_proposition"] = _decision(
        "value_proposition",
        basis=["product.feature_benefit_value.pickup availability", "audience.desires"],
    )
    payload["marketing_angle"] = _decision(
        "marketing_angle",
        basis=["product.feature_benefit_value.pickup availability", "audience.motivation"],
    )
    payload["desired_reaction"] = _decision(
        "desired_reaction",
        basis=["semantic_contract.goal", "audience.motivation"],
    )
    payload["cta_strategy"] = _decision(
        "cta_strategy",
        basis=["semantic_contract.cta_intent", "semantic_contract.goal"],
    )
    payload["single_minded_message"] = _decision(
        "single_minded_message",
        decision="Land and drive without waiting.",
        basis=[
            "product.feature_benefit_value.pickup availability",
            "audience.customer_tension",
        ],
    )
    payload["message_framework"] = {
        "framework": "pas",
        "rationale": "A stated arrival problem can be agitated honestly.",
        "basis": ["audience.pain_points"],
    }
    for name, value in overrides.items():
        payload[name] = value
    return payload


class _StrategyLLM:
    def __init__(self, **overrides: Any) -> None:
        self.requests: list[LLMRequest] = []
        self._payload = _strategy_payload(**overrides)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=json.dumps(self._payload),
            provider="test-llm",
            model="strategist-test",
        )


class _RepairingStrategyLLM:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []
        self._invalid = _strategy_payload(
            business_objective=_decision(
                "business_objective",
                principle=MarketingPrinciple.POSITIONING.value,
            ),
            targeting=_decision(
                "targeting",
                decision="Target an invented executive segment.",
                basis=[TARGET_BASIS],
            ),
            single_minded_message=_decision(
                "single_minded_message",
                decision="Book now and get 24/7 airport pickup.",
                basis=["product.usp_candidates.1", "audience.customer_tension"],
            ),
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        payload = self._invalid if len(self.requests) == 1 else _strategy_payload()
        return LLMResponse(
            text=json.dumps(payload),
            provider="test-llm",
            model="strategist-repair-test",
        )


def _context() -> AgentExecutionContext:
    return AgentExecutionContext(
        invocation=InvocationContext(
            correlation_id=uuid4(), post_id=uuid4(), generation_id=uuid4()
        ),
        agent_name="marketing_strategist",
        attempt=1,
    )


async def _run(payload: MarketingStrategyInput, llm: _StrategyLLM) -> MarketingStrategy:
    return await MarketingStrategistAgent(llm).execute(payload, None, _context())


# --------------------------------------------------------------------------
# The strategy itself
# --------------------------------------------------------------------------


async def test_every_ticket_field_is_decided_with_a_rationale() -> None:
    contract = _contract()
    payload = await _input(contract)
    llm = _StrategyLLM()

    strategy = await _run(payload, llm)

    assert isinstance(strategy, MarketingStrategy)
    for name, decision in strategy.decisions().items():
        assert decision.decision, name
        assert decision.rationale, name
        assert decision.basis, name
    assert strategy.message_framework.framework is MessageFramework.PAS
    assert strategy.message_framework.rationale
    assert strategy.contract_fingerprint == contract.fingerprint
    provider_input = json.loads(llm.requests[0].messages[-1].content)["source"]
    assert provider_input["brief"]["style_preferences"] == ["clean", "trustworthy"]
    assert provider_input["semantic_contract"]["constraints"] == list(contract.constraints)


async def test_invalid_first_output_gets_one_grounded_correction_pass() -> None:
    payload = await _input()
    llm = _RepairingStrategyLLM()

    strategy = await MarketingStrategistAgent(llm).execute(payload, None, _context())

    assert isinstance(strategy, MarketingStrategy)
    assert len(llm.requests) == 2
    correction = json.loads(llm.requests[1].messages[-1].content)
    assert "business_objective must apply the stp principle" in correction["validation_error"]
    assert (
        "targeting must name one of the supplied audience segments"
        in correction["validation_error"]
    )
    assert (
        "single-minded message must not combine multiple promises" in correction["validation_error"]
    )
    assert "previous_output" in correction


async def test_each_decision_answers_to_its_own_principle() -> None:
    """A targeting call labelled 'positioning' is a mislabelled targeting call."""
    payload = await _input()

    strategy = await _run(payload, _StrategyLLM())

    assert strategy.segmentation.principle is MarketingPrinciple.STP
    assert strategy.positioning.principle is MarketingPrinciple.POSITIONING
    assert strategy.usp.principle is MarketingPrinciple.USP
    assert strategy.value_proposition.principle is MarketingPrinciple.VALUE_PROPOSITION
    assert strategy.single_minded_message.principle is MarketingPrinciple.MESSAGE_STRATEGY
    assert strategy.cta_strategy.principle is MarketingPrinciple.CTA_STRATEGY


async def test_a_relabelled_principle_is_rejected() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        targeting=_decision(
            "targeting",
            decision=f"Target the {SEGMENT} segment first.",
            basis=[TARGET_BASIS],
            principle=MarketingPrinciple.POSITIONING.value,
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


async def test_unknown_model_basis_is_removed_and_required_grounding_is_attached() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        positioning=_decision("positioning", basis=["research.market.industry_report_2026"])
    )

    strategy = await _run(payload, llm)

    assert "research.market.industry_report_2026" not in strategy.positioning.basis
    assert any(
        reference in {TARGET_BASIS, "audience.target"} for reference in strategy.positioning.basis
    )
    assert any(reference.startswith("product.") for reference in strategy.positioning.basis)


async def test_research_evidence_is_citable_by_dimension() -> None:
    """The allowlist is built from analyses that actually produced insights."""
    payload = await _input()
    llm = _StrategyLLM(
        positioning=_decision(
            "positioning",
            basis=[TARGET_BASIS, "product.usp_candidates.1", "research.market.category"],
        ),
    )

    strategy = await _run(payload, llm)

    assert "research.market.category" in strategy.positioning.basis


async def test_the_objective_belongs_to_the_brief() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        business_objective=_decision("business_objective", basis=["brand.identity_summary"])
    )

    strategy = await _run(payload, llm)

    assert "semantic_contract.goal" in strategy.business_objective.basis


async def test_the_cta_strategy_serves_the_declared_intent() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        cta_strategy=_decision(
            "cta_strategy",
            basis=["audience.motivation", "semantic_contract.goal"],
        )
    )

    strategy = await _run(payload, llm)

    assert "semantic_contract.cta_intent" in strategy.cta_strategy.basis
    assert "semantic_contract.goal" in strategy.cta_strategy.basis


async def test_targeting_must_name_a_supplied_segment() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        targeting=_decision(
            "targeting",
            decision="Target frequent business travellers.",
            basis=[TARGET_BASIS],
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


async def test_the_usp_must_descend_from_the_product() -> None:
    """A USP is what this product verifiably does better, not a good phrase."""
    payload = await _input()
    llm = _StrategyLLM(usp=_decision("usp", basis=["audience.desires"]))

    strategy = await _run(payload, llm)

    assert any(
        reference.startswith(("product.usp_candidates.", "product.feature_benefit_value."))
        for reference in strategy.usp.basis
    )


async def test_the_customer_tension_builds_on_the_audience_tension() -> None:
    payload = await _input()
    llm = _StrategyLLM(customer_tension=_decision("customer_tension", basis=["audience.needs"]))

    strategy = await _run(payload, llm)

    assert "audience.customer_tension" in strategy.customer_tension.basis


async def test_application_attaches_field_specific_evidence_foundations() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        positioning=_decision("positioning", decision=f"Serve the {SEGMENT} segment."),
        value_proposition=_decision("value_proposition"),
        marketing_angle=_decision("marketing_angle"),
    )

    strategy = await _run(payload, llm)

    assert any(
        reference in {TARGET_BASIS, "audience.target"} for reference in strategy.positioning.basis
    )
    assert any(reference.startswith("product.") for reference in strategy.positioning.basis)
    assert any(reference.startswith("product.") for reference in strategy.value_proposition.basis)
    assert any(reference.startswith("audience.") for reference in strategy.value_proposition.basis)
    assert any(reference.startswith("product.") for reference in strategy.marketing_angle.basis)
    assert any(reference.startswith("audience.") for reference in strategy.marketing_angle.basis)


# --------------------------------------------------------------------------
# Message discipline
# --------------------------------------------------------------------------


async def test_the_single_minded_message_carries_one_idea() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        single_minded_message=_decision(
            "single_minded_message",
            decision="Land and drive without waiting. Our fleet is also the newest in Kosovo.",
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


async def test_one_sentence_with_multiple_promises_is_rejected() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        single_minded_message=_decision(
            "single_minded_message",
            decision="Book now and get 24/7 airport pickup.",
            basis=["product.usp_candidates.1", "audience.customer_tension"],
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


async def test_a_framework_may_be_declined() -> None:
    """Forcing PAS onto a brief with no agitatable problem is worse than none."""
    payload = await _input()
    llm = _StrategyLLM(
        message_framework={
            "framework": "none",
            "rationale": "Neither structure suits a single-fact convenience message.",
            "basis": ["semantic_contract.goal"],
        }
    )

    strategy = await _run(payload, llm)

    assert strategy.message_framework.framework is MessageFramework.NONE
    assert strategy.message_framework.rationale


async def test_pas_is_grounded_in_a_real_pain_point_or_tension() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        message_framework={
            "framework": "pas",
            "rationale": "Use a problem structure.",
            "basis": ["semantic_contract.goal"],
        }
    )

    strategy = await _run(payload, llm)

    assert any(
        reference in {"audience.pain_points", "audience.customer_tension"}
        for reference in strategy.message_framework.basis
    )


async def test_aida_is_grounded_in_both_objective_and_audience_context() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        message_framework={
            "framework": "aida",
            "rationale": "Earn attention before asking for action.",
            "basis": ["semantic_contract.goal"],
        }
    )

    strategy = await _run(payload, llm)

    assert "semantic_contract.goal" in strategy.message_framework.basis
    assert any(
        reference.startswith("audience.") or reference == "semantic_contract.audience"
        for reference in strategy.message_framework.basis
    )


# --------------------------------------------------------------------------
# Contract safety
# --------------------------------------------------------------------------


async def test_a_forbidden_claim_fails_the_whole_strategy() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        value_proposition=_decision(
            "value_proposition",
            decision="Positioned as the cheapest rental in Kosovo.",
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


@pytest.mark.parametrize(
    "claim",
    [
        "Position BMW as the guaranteed luxury replacement.",
        "Mercedes is the preferred rental product.",
        "Promise a free upgrade to every customer.",
        "Advertise a fabricated EUR 19/day rate.",
        "Present the fleet as the newest in Kosovo.",
    ],
)
async def test_unsupported_identity_numeric_and_absolute_claims_fail(claim: str) -> None:
    payload = await _input()
    llm = _StrategyLLM(
        positioning=_decision(
            "positioning",
            decision=claim,
            basis=[TARGET_BASIS, "product.usp_candidates.1"],
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


async def test_supplied_identity_and_numeric_facts_remain_usable() -> None:
    payload = await _input()
    llm = _StrategyLLM(
        value_proposition=_decision(
            "value_proposition",
            decision="Prishtina Drive provides 24/7 airport pickup for arrival certainty.",
            basis=[
                "product.feature_benefit_value.pickup availability",
                "audience.desires",
            ],
        )
    )

    strategy = await _run(payload, llm)

    assert "24/7 airport pickup" in strategy.value_proposition.decision


@pytest.mark.parametrize(
    "decision",
    [
        "Copy the competitor's booking message.",
        "Replace the product with a luxury vehicle.",
        "Use a red headline and compact typography.",
    ],
)
async def test_strategy_cannot_cross_identity_competitor_or_execution_boundaries(
    decision: str,
) -> None:
    payload = await _input()
    llm = _StrategyLLM(
        marketing_angle=_decision(
            "marketing_angle",
            decision=decision,
            basis=["product.usp_candidates.1", "audience.motivation"],
        )
    )

    with pytest.raises(ProviderResponseError):
        await _run(payload, llm)


async def test_inputs_that_describe_different_posts_are_refused() -> None:
    """The first stage where four upstream outputs could silently disagree."""
    contract = _contract()
    payload = await _input(contract)
    drifted = payload.model_copy(
        update={"audience": payload.audience.model_copy(update={"contract_fingerprint": "0" * 64})}
    )

    with pytest.raises(ValueError, match="inputs disagree on the contract"):
        await _run(drifted, _StrategyLLM())


async def test_evidence_gaps_travel_with_the_strategy() -> None:
    """A later stage must inherit the uncertainty, not just the decisions."""
    payload = await _input()

    strategy = await _run(payload, _StrategyLLM())

    assert strategy.limitations
    assert any("research has not yet validated" in item for item in strategy.limitations)


async def test_stage_payload_validates_every_declared_upstream_section() -> None:
    contract = _contract()
    research = await _research(contract)
    state = {
        PostWorkflowSection.BRIEF.value: _brief(contract).model_dump(mode="json"),
        PostWorkflowSection.SEMANTIC_CONTRACT.value: contract.to_dict(),
        PostWorkflowSection.BRAND.value: _brand(contract).model_dump(mode="json"),
        PostWorkflowSection.PRODUCT.value: _product(contract).model_dump(mode="json"),
        PostWorkflowSection.AUDIENCE.value: _audience(contract).model_dump(mode="json"),
        PostWorkflowSection.RESEARCH.value: research.model_dump(mode="json"),
    }

    payload = _agent_payload(state)

    assert payload["brief"]["style_preferences"] == ["clean", "trustworthy"]
    assert payload["semantic_contract"]["fingerprint"] == contract.fingerprint
    with pytest.raises(ValueError):
        _agent_payload({key: value for key, value in state.items() if key != "brand"})


def test_supervisor_declares_every_marketing_strategy_input() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.MARKETING_STRATEGY)

    assert set(policy.required_sections) == {
        PostWorkflowSection.BRIEF,
        PostWorkflowSection.SEMANTIC_CONTRACT,
        PostWorkflowSection.BRAND,
        PostWorkflowSection.PRODUCT,
        PostWorkflowSection.AUDIENCE,
        PostWorkflowSection.RESEARCH,
    }


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_states_the_rules_the_application_enforces() -> None:
    prompt = _system_prompt(
        {
            "semantic_contract.goal",
            "audience.customer_tension",
            TARGET_BASIS,
            "product.usp_candidates.1",
        }
    )

    assert "EVERY DECISION NEEDS A RATIONALE" in prompt
    assert "semantic_contract.goal" in prompt, "the allowlist travels with the prompt"
    assert "ONE sentence" in prompt
    assert "usp_candidate" in prompt
    assert "field-specific evidence requirements" in prompt
    assert "copying ONE EXACT identifier" in prompt
    assert "Never introduce a new brand" in prompt
    for downstream in ("headlines", "captions", "art direction"):
        assert downstream in prompt, f"{downstream} belongs to a later stage"


def test_prompt_resolves_prefix_rules_to_concrete_basis_ids() -> None:
    allowed = {
        TARGET_BASIS,
        "product.usp_candidates.1",
        "product.feature_benefit_value.pickup availability",
    }

    requirements = _resolved_basis_requirements(allowed)

    assert requirements["targeting"] == [[TARGET_BASIS]]
    assert requirements["positioning"] == [
        [TARGET_BASIS],
        [
            "product.feature_benefit_value.pickup availability",
            "product.usp_candidates.1",
        ],
    ]
    assert "audience.segments." not in requirements["targeting"][0]
