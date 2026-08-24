"""Run the Ticket 17 Audience Intelligence Agent against the configured LLM."""

import asyncio
import json
import sys
from pathlib import Path

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_llm_provider  # noqa: E402
from app.modules.posts.agents.audience_research import (  # noqa: E402
    AUDIENCE_INTELLIGENCE_AGENT_NAME,
    AudienceIntelligenceInput,
    register_audience_intelligence_agent,
)
from app.modules.posts.agents.brand_product import (  # noqa: E402
    BrandAnalysis,
    ProductAnalysis,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402


def _sample_input() -> AudienceIntelligenceInput:
    contract = PostSemanticContract.create(
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
        required_assets=[],
        constraints=["Do not replace the product or logo"],
    )
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
        required_assets=[],
        contract_fingerprint=contract.fingerprint,
    )
    return AudienceIntelligenceInput(
        semantic_contract=contract.to_dict(),
        brand=brand,
        product=product,
    )


async def _run() -> None:
    settings = get_settings()
    running_in_container = Path("/.dockerenv").exists()
    if (
        not running_in_container
        and settings.ollama_base_url.rstrip("/") == "http://host.docker.internal:11434"
    ):
        settings = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})

    runtime = AgentRuntime(ToolRegistry())
    register_audience_intelligence_agent(runtime, create_llm_provider(settings))
    result = await runtime.run(AUDIENCE_INTELLIGENCE_AGENT_NAME, _sample_input())
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_run())
