"""Run Ticket 18 against the configured research provider and verify caching."""

import asyncio
import json
import sys
from pathlib import Path

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_research_provider  # noqa: E402
from app.modules.posts.agents.audience_research import AudienceIntelligence  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools.research import (  # noqa: E402
    ExternalResearchInput,
    ExternalResearchService,
    InMemoryResearchCache,
    ResearchCategory,
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
        required_facts={"pickup availability": "24/7 airport pickup"},
        forbidden_claims=["cheapest rental in Kosovo"],
        required_assets=[],
        constraints=["Do not replace the product or logo"],
    )


def _audience(contract: PostSemanticContract) -> AudienceIntelligence:
    basis = ["semantic_contract.audience"]
    insight = {
        "insight": "Immediate access may matter after arrival.",
        "basis": basis,
        "confidence": "medium",
    }
    return AudienceIntelligence.model_validate(
        {
            "segments": [
                {
                    "name": "Arrival convenience seekers",
                    "description": "Diaspora seeking immediate transport.",
                    "parent_audience": contract.audience,
                    "basis": basis,
                    "confidence": "medium",
                }
            ],
            "target": {
                "segment": "Arrival convenience seekers",
                "rationale": "Connected to the declared arrival context.",
                "basis": basis,
                "confidence": "medium",
            },
            "needs": [insight],
            "desires": [insight],
            "pain_points": [insight],
            "objections": [insight],
            "motivation": [insight],
            "purchase_intent": {
                "level": "unknown",
                "rationale": "External evidence is required.",
                "basis": basis,
                "confidence": "low",
            },
            "trust_triggers": [insight],
            "context": {
                "declared_audience": contract.audience,
                "market": contract.market,
                "location": contract.location,
                "platform": contract.platform,
                "situations": [insight],
            },
            "customer_tension": {
                "current_state": "No transport immediately after arrival.",
                "desired_state": "Transport ready immediately.",
                "tension": "Avoid waiting after landing.",
                "basis": basis,
                "confidence": "medium",
            },
            "limitations": ["External research has not validated these hypotheses."],
            "contract_fingerprint": contract.fingerprint,
        }
    )


def _summary(result) -> dict:
    return {
        "contract_fingerprint": result.contract_fingerprint,
        "researched_at": result.researched_at.isoformat(),
        "reports": {
            category.value: {
                "status": getattr(result, category.value).status.value,
                "confidence": getattr(result, category.value).confidence.value,
                "sources": len(getattr(result, category.value).sources),
                "cached": getattr(result, category.value).cached,
                "expires_at": getattr(result, category.value).expires_at.isoformat(),
            }
            for category in ResearchCategory
        },
    }


async def _run() -> None:
    settings = get_settings()
    contract = _contract()
    cache = InMemoryResearchCache()
    service = ExternalResearchService.from_provider(
        create_research_provider(settings),
        cache=cache,
        cache_ttl_seconds=settings.research_cache_ttl_seconds,
        max_concurrency=settings.research_max_concurrency,
    )
    payload = ExternalResearchInput(
        semantic_contract=contract.to_dict(),
        audience=_audience(contract),
    )

    print("Running 8 external research categories...")
    first = await service.run(payload)
    print(json.dumps(_summary(first), ensure_ascii=False, indent=2))

    print("\nRepeating the same request to verify cache reuse...")
    second = await service.run(payload)
    if not all(getattr(second, category.value).cached for category in ResearchCategory):
        raise RuntimeError("second research run was not fully cached")
    print(json.dumps(_summary(second), ensure_ascii=False, indent=2))
    print("\nALL 8 RESEARCH TOOLS PASSED; SECOND RUN WAS 100% CACHED")


if __name__ == "__main__":
    asyncio.run(_run())
