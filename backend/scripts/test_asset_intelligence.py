"""Run Ticket 16 Asset Intelligence against the configured LLM provider."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_llm_provider  # noqa: E402
from app.modules.posts.agents.asset_intelligence import (  # noqa: E402
    ASSET_INTELLIGENCE_AGENT_NAME,
    AssetIntelligenceInput,
    register_asset_intelligence_agent,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402

LOGO_ID = UUID("11111111-1111-4111-8111-111111111111")
VEHICLE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _sample_input() -> AssetIntelligenceInput:
    contract = PostSemanticContract.create(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Skoda Fabia rental",
        primary_entity="Skoda Fabia",
        goal="Drive bookings",
        audience="Diaspora arriving in Kosovo",
        market="Kosovo",
        location="Prishtina airport",
        offer="From EUR 35/day",
        cta_intent="Book now",
        platform="Instagram",
        language="Albanian",
        required_facts={"vehicle model": "Skoda Fabia"},
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=[LOGO_ID, VEHICLE_ID],
        constraints=["Do not replace the vehicle or logo"],
    )
    return AssetIntelligenceInput(
        semantic_contract=contract.to_dict(),
        latest_message=(
            "Kjo është logoja jonë. Kjo është vetura që duhet të përdoret. "
            "Mos e zëvendëso as logon, as veturën."
        ),
        attachments=[
            {
                "id": LOGO_ID,
                "declared_role": "logo",
                "original_filename": "prishtina-drive-logo.png",
                "mime_type": "image/png",
                "width": 1200,
                "height": 400,
                "metadata": {"source": "client"},
            },
            {
                "id": VEHICLE_ID,
                "declared_role": "vehicle",
                "original_filename": "skoda-fabia.png",
                "mime_type": "image/png",
                "width": 1600,
                "height": 1000,
                "metadata": {"source": "client"},
            },
        ],
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
    register_asset_intelligence_agent(runtime, create_llm_provider(settings))
    result = await runtime.run(ASSET_INTELLIGENCE_AGENT_NAME, _sample_input())
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_run())
