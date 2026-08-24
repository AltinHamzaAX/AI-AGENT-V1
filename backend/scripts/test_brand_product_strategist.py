"""Run the Ticket 15 Brand & Product Strategist against the configured LLM."""

import asyncio
import json
import sys
from pathlib import Path

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_llm_provider  # noqa: E402
from app.modules.posts.agents.brand_product import (  # noqa: E402
    BRAND_PRODUCT_AGENT_NAME,
    BrandProductInput,
    register_brand_product_agent,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402


def _sample_contract() -> PostSemanticContract:
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
        required_assets=[],
        constraints=["Do not replace the product or logo"],
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
    register_brand_product_agent(runtime, create_llm_provider(settings))
    result = await runtime.run(
        BRAND_PRODUCT_AGENT_NAME,
        BrandProductInput(semantic_contract=_sample_contract().to_dict()),
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_run())
