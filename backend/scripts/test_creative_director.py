"""Run Ticket 24 against the configured LLM with approved upstream fixtures."""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_marketing_strategist import (  # noqa: E402
    _audience,
    _brand,
    _contract,
    _synthetic_research,
)

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_provider_bundle  # noqa: E402
from app.modules.posts.agents.creative_director import (  # noqa: E402
    CREATIVE_DIRECTOR_AGENT_NAME,
    CreativeDirection,
    register_creative_director_agent,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.agents.marketing_strategist import (  # noqa: E402
    MarketingPrinciple,
    MarketingStrategy,
    MessageFramework,
    MessageFrameworkChoice,
    StrategicDecision,
)
from app.modules.posts.tools import ToolRegistry  # noqa: E402


def _decision(
    decision: str,
    rationale: str,
    principle: MarketingPrinciple,
    basis: list[str],
) -> StrategicDecision:
    return StrategicDecision(
        decision=decision,
        rationale=rationale,
        principle=principle,
        basis=basis,
    )


def _approved_strategy(contract) -> MarketingStrategy:
    """A reviewed Ticket 21 output keeps this a focused Ticket 24 test."""
    audience_basis = ["audience.customer_tension"]
    product_basis = ["product.feature_benefit_value.pickup availability"]
    return MarketingStrategy(
        business_objective=_decision(
            "Increase airport car-rental bookings from arriving diaspora travellers.",
            "This directly serves the approved booking objective.",
            MarketingPrinciple.STP,
            ["semantic_contract.goal"],
        ),
        segmentation=_decision(
            "Segment by arrival context and immediate mobility need.",
            "Arrival context is more actionable than demographics alone.",
            MarketingPrinciple.STP,
            ["semantic_contract.audience"],
        ),
        targeting=_decision(
            "Prioritize Arrival convenience seekers.",
            "They experience the strongest need immediately after landing.",
            MarketingPrinciple.STP,
            ["audience.target"],
        ),
        positioning=_decision(
            "The dependable airport mobility option ready when the traveller lands.",
            "It connects the target's arrival tension to verified pickup availability.",
            MarketingPrinciple.POSITIONING,
            ["audience.target", *product_basis],
        ),
        customer_insight=_decision(
            "After a long flight, uncertainty about onward transport feels heavier than usual.",
            "The supplied audience evidence centers on immediate access after arrival.",
            MarketingPrinciple.CUSTOMER_INSIGHT,
            audience_basis,
        ),
        customer_tension=_decision(
            "The traveller has arrived but cannot feel the trip has started "
            "until transport is secured.",
            "This preserves the approved current-state versus desired-state tension.",
            MarketingPrinciple.CUSTOMER_INSIGHT,
            audience_basis,
        ),
        usp=_decision(
            "Round-the-clock airport pickup.",
            "This is a verified product capability tied to a named source fact.",
            MarketingPrinciple.USP,
            product_basis,
        ),
        value_proposition=_decision(
            "Move from landing to the road with dependable pickup availability.",
            "It translates the verified service into immediate customer value.",
            MarketingPrinciple.VALUE_PROPOSITION,
            [*product_basis, *audience_basis],
        ),
        marketing_angle=_decision(
            "Turn airport arrival from a waiting moment into the start of the journey.",
            "The angle resolves the customer tension through the verified service benefit.",
            MarketingPrinciple.MESSAGE_STRATEGY,
            [*product_basis, *audience_basis],
        ),
        single_minded_message=_decision(
            "Your onward journey can begin the moment you land.",
            "One customer-facing promise keeps the communication focused.",
            MarketingPrinciple.MESSAGE_STRATEGY,
            [*product_basis, *audience_basis],
        ),
        desired_reaction=_decision(
            "Feel reassured and choose to arrange the car before arrival.",
            "Reassurance supports the declared booking objective.",
            MarketingPrinciple.MESSAGE_STRATEGY,
            ["semantic_contract.goal", *audience_basis],
        ),
        cta_strategy=_decision(
            "Prompt a direct booking action after establishing arrival certainty.",
            "The action follows the approved Book now intent and booking objective.",
            MarketingPrinciple.CTA_STRATEGY,
            ["semantic_contract.cta_intent", "semantic_contract.goal"],
        ),
        message_framework=MessageFrameworkChoice(
            framework=MessageFramework.PAS,
            rationale="The real arrival tension can be resolved without exaggeration.",
            basis=audience_basis,
        ),
        limitations=["Research evidence in this focused test is synthetic."],
        contract_fingerprint=contract.fingerprint,
    )


async def _run() -> None:
    providers = create_provider_bundle(get_settings())
    contract = _contract()
    brand = _brand(contract)
    audience = _audience(contract)
    research = _synthetic_research(contract)

    runtime = AgentRuntime(ToolRegistry())
    register_creative_director_agent(runtime, providers.creative_llm)

    strategy = _approved_strategy(contract)
    print("Approved upstream strategy loaded.", flush=True)
    print(f"Creative model: {providers.creative_llm._model}", flush=True)
    print("Building creative direction with the configured LLM (usually 60-180s)...", flush=True)
    started = time.perf_counter()
    direction = await runtime.run(
        CREATIVE_DIRECTOR_AGENT_NAME,
        {
            "marketing_strategy": strategy.model_dump(mode="json"),
            "audience": audience.model_dump(mode="json"),
            "brand": brand.model_dump(mode="json"),
            "research": research.model_dump(mode="json"),
            "semantic_contract": contract.to_dict(),
        },
        invocation=None,
    )
    if not isinstance(direction, CreativeDirection):
        raise SystemExit(f"unexpected output type: {type(direction).__name__}")
    print(f"    Ready in {time.perf_counter() - started:.0f}s\n")

    print(f"Selected: {direction.selected_big_idea_id}")
    for territory in direction.creative_territories:
        print(f"  {territory.id} [{territory.angle.value}]: {territory.name}")
    for candidate in direction.big_idea_candidates:
        marker = " [SELECTED]" if candidate.id == direction.selected_big_idea_id else ""
        print(f"  {candidate.id}: {candidate.name} - {candidate.evaluation.total}/70{marker}")
        print(f"      weakness: {candidate.evaluation.weakness}")
    print("Quality gate:")
    for check in direction.quality_gate.checks:
        print(f"  {check.dimension}: {check.score}/{check.threshold} required")
    print(f"Rationale: {direction.creative_rationale}")
    if "--json" in sys.argv:
        print(json.dumps(direction.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print("\nCREATIVE DIRECTOR VERIFIED: no final poster was produced")


if __name__ == "__main__":
    asyncio.run(_run())
