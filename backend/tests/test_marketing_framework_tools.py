"""Ticket 22: bounded, deterministic frameworks owned by Marketing Strategist."""

import json

import pytest
from test_marketing_strategist import _input, _StrategyLLM

from app.modules.posts.agents.framework import AgentRuntime
from app.modules.posts.agents.marketing_strategist import (
    MARKETING_STRATEGIST_AGENT_NAME,
    MARKETING_STRATEGIST_DEFINITION,
    MarketingStrategy,
    register_marketing_strategist_agent,
)
from app.modules.posts.domain.contracts import InvocationContext, ToolCapability, ToolCategory
from app.modules.posts.tools import ToolRegistry
from app.modules.posts.tools.marketing import (
    CTA_ENGINE,
    FEATURE_BENEFIT_MAPPER,
    MARKETING_FRAMEWORK_TOOL_NAMES,
    MESSAGE_STRATEGY_ENGINE,
    POSITIONING_BUILDER,
    STP_ENGINE,
    USP_EXTRACTOR,
    VALUE_PROPOSITION_BUILDER,
    DirectMarketingFrameworkGateway,
    FeatureBenefitMapResult,
    FrameworkKind,
    MarketingFrameworkInput,
    register_marketing_framework_tools,
)


async def _tool_input():
    payload = await _input()
    return payload, {
        "semantic_contract": payload.semantic_contract,
        "product": payload.product.model_dump(mode="json"),
        "audience": payload.audience.model_dump(mode="json"),
    }


def test_ticket_registers_exactly_seven_read_only_marketing_tools() -> None:
    registry = ToolRegistry()
    register_marketing_framework_tools(registry)
    definitions = registry.definitions()

    assert {definition.tool_name for definition in definitions} == MARKETING_FRAMEWORK_TOOL_NAMES
    assert len(definitions) == 7
    assert MARKETING_STRATEGIST_DEFINITION.allowed_tools == MARKETING_FRAMEWORK_TOOL_NAMES
    for definition in definitions:
        assert definition.category is ToolCategory.MARKETING
        assert definition.allowed_agents == frozenset({MARKETING_STRATEGIST_AGENT_NAME})
        assert definition.security.capabilities == frozenset({ToolCapability.READ_CONTEXT})


@pytest.mark.asyncio
async def test_frameworks_preserve_the_feature_to_value_to_positioning_chain() -> None:
    _, payload = await _tool_input()
    gateway = DirectMarketingFrameworkGateway()

    mapping = FeatureBenefitMapResult.model_validate(
        await gateway.invoke(FEATURE_BENEFIT_MAPPER, payload)
    )
    usp = await gateway.invoke(USP_EXTRACTOR, payload)
    positioning = await gateway.invoke(POSITIONING_BUILDER, payload)
    value = await gateway.invoke(VALUE_PROPOSITION_BUILDER, payload)

    assert mapping.mappings[0].feature == "24/7 airport pickup"
    assert mapping.mappings[0].benefit == "No waiting after landing"
    assert mapping.mappings[0].customer_value == "Convenience and certainty"
    assert mapping.mappings[0].basis == [
        "product.feature_benefit_value.pickup availability",
        "product.verified_facts.pickup availability",
    ]
    assert usp.candidates[0].value == "Round-the-clock airport pickup"
    assert positioning.target.value == "Arrival convenience seekers"
    assert any(item.value == "Convenience and certainty" for item in positioning.differentiators)
    assert value.target.value == positioning.target.value
    assert any(item.value == "Convenience and certainty" for item in value.customer_values)


@pytest.mark.asyncio
async def test_message_and_cta_engines_choose_only_grounded_options() -> None:
    _, payload = await _tool_input()
    gateway = DirectMarketingFrameworkGateway()

    stp = await gateway.invoke(STP_ENGINE, payload)
    message = await gateway.invoke(MESSAGE_STRATEGY_ENGINE, payload)
    cta = await gateway.invoke(CTA_ENGINE, payload)

    assert stp.objective.value == "Drive bookings"
    assert stp.target.value in {segment.value for segment in stp.segments}
    assert message.eligible_frameworks == [
        FrameworkKind.PAS,
        FrameworkKind.AIDA,
        FrameworkKind.NONE,
    ]
    assert message.single_message_required is True
    assert "audience.customer_tension" in message.customer_tension.basis
    assert cta.intent.value == "Book now"
    assert cta.intent.basis == ["semantic_contract.cta_intent"]
    assert cta.objective.basis == ["semantic_contract.goal"]


@pytest.mark.asyncio
async def test_tools_fail_closed_on_contract_drift_and_ungrounded_product_facts() -> None:
    payload, tool_input = await _tool_input()
    gateway = DirectMarketingFrameworkGateway()
    drifted = {
        **tool_input,
        "product": payload.product.model_copy(
            update={"contract_fingerprint": "0" * 64}
        ).model_dump(mode="json"),
    }
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        MarketingFrameworkInput.model_validate(drifted)

    product = payload.product.model_dump(mode="json")
    product["feature_benefit_value"][0]["source_fact"] = "invented capability"
    with pytest.raises(ValueError, match="unknown fact"):
        await gateway.invoke(FEATURE_BENEFIT_MAPPER, {**tool_input, "product": product})


@pytest.mark.asyncio
async def test_strategist_invokes_all_tools_and_receives_structured_scaffolding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload, _ = await _tool_input()
    llm = _StrategyLLM()
    registry = ToolRegistry()
    register_marketing_framework_tools(registry)
    runtime = AgentRuntime(registry)
    register_marketing_strategist_agent(runtime, llm)

    with caplog.at_level("INFO"):
        output = await runtime.run(
            MARKETING_STRATEGIST_AGENT_NAME,
            payload,
            invocation=InvocationContext(),
        )

    assert isinstance(output, MarketingStrategy)
    provider_source = json.loads(llm.requests[0].messages[-1].content)["source"]
    frameworks = provider_source["marketing_framework_tools"]
    assert set(frameworks) == {
        "stp",
        "feature_benefit",
        "usp",
        "positioning",
        "value_proposition",
        "message_strategy",
        "cta",
    }
    succeeded = {
        record.tool_name
        for record in caplog.records
        if record.getMessage() == "posts.tool.succeeded"
    }
    assert succeeded == MARKETING_FRAMEWORK_TOOL_NAMES
