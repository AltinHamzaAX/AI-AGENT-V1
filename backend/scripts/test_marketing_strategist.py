"""Run the Ticket 21 Marketing Strategist against the configured LLM.

Research is synthesised by default so this stays a test of the strategist and
finishes in one model call. Pass --live-research to assemble the real eight
research reports first, which costs the full research stage.

Run from backend, with the Ollama host overridden for a non-Docker shell:

    $env:OLLAMA_BASE_URL = "http://localhost:11434"
    .venv\\Scripts\\python.exe scripts\\test_marketing_strategist.py
"""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_provider_bundle  # noqa: E402
from app.modules.posts.agents.audience_research import AudienceIntelligence  # noqa: E402
from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis  # noqa: E402
from app.modules.posts.agents.client_understanding import ClientUnderstandingBrief  # noqa: E402
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.agents.marketing_strategist import (  # noqa: E402
    MARKETING_STRATEGIST_AGENT_NAME,
    STRATEGY_DECISIONS,
    MarketingStrategy,
    register_marketing_strategist_agent,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402
from app.modules.posts.tools.research import (  # noqa: E402
    ExternalResearchInput,
    ExternalResearchResult,
    ExternalResearchService,
    ResearchCategory,
    ResearchConfidence,
    ResearchStatus,
)
from app.modules.posts.tools.research.schemas import (  # noqa: E402
    ResearchReport,
    ResearchSource,
)

NOW = datetime.now(UTC)


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
        required_assets=[],
        constraints=["Do not replace the product or logo"],
    )


def _brand(contract: PostSemanticContract) -> BrandAnalysis:
    return BrandAnalysis(
        company=contract.company,
        name=contract.brand,
        identity_summary="A dependable airport mobility brand for arriving travellers.",
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
                "benefit": "No waiting after a late landing",
                "customer_value": "Certainty at an uncertain moment",
            },
            {
                "source_fact": "vehicle class",
                "feature": "compact automatic car",
                "benefit": "Easy to drive on unfamiliar roads",
                "customer_value": "Less effort after a long flight",
            },
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


def _audience(contract: PostSemanticContract) -> AudienceIntelligence:
    basis = ["semantic_contract.audience"]
    insight = {
        "insight": "Immediate access to a car matters most right after landing.",
        "basis": basis,
        "confidence": "medium",
    }
    return AudienceIntelligence.model_validate(
        {
            "segments": [
                {
                    "name": "Arrival convenience seekers",
                    "description": "Diaspora travellers who need transport the moment they land.",
                    "parent_audience": contract.audience,
                    "basis": basis,
                    "confidence": "medium",
                }
            ],
            "target": {
                "segment": "Arrival convenience seekers",
                "rationale": "Directly connected to the declared arrival context.",
                "basis": basis,
                "confidence": "medium",
            },
            "needs": [insight],
            "desires": [insight],
            "pain_points": [insight],
            "objections": [insight],
            "motivation": [insight],
            "purchase_intent": {
                "level": "medium",
                "rationale": "Arrival transport is decided close to the trip.",
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
                "current_state": "Landing with no transport arranged.",
                "desired_state": "Keys in hand within minutes of landing.",
                "tension": "The wait after arrival is the worst part of the trip.",
                "basis": basis,
                "confidence": "medium",
            },
            "limitations": ["External research has not yet validated these hypotheses."],
            "contract_fingerprint": contract.fingerprint,
        }
    )


def _synthetic_research(contract: PostSemanticContract) -> ExternalResearchResult:
    """Eight minimal reports, so the strategist has real basis identifiers."""
    reports = {}
    for category in ResearchCategory:
        source = ResearchSource(
            title=f"Observed {category.value} evidence",
            url=f"https://evidence.example/{category.value}",
            excerpt=(
                "Providers publish daily rates and pickup terms on their own pages, "
                "and arriving customers compare price clarity before booking."
            ),
            retrieved_at=NOW,
            quality_score=0.6,
        )
        reports[category.value] = ResearchReport(
            category=category,
            status=ResearchStatus.SUCCEEDED,
            query=f"{contract.primary_entity} {category.value}",
            provider="synthetic",
            confidence=ResearchConfidence.MEDIUM,
            findings=[
                {
                    "statement": "Arriving customers compare price clarity before booking.",
                    "source_url": str(source.url),
                    "confidence": "medium",
                }
            ],
            sources=[source],
            researched_at=NOW,
            expires_at=NOW + timedelta(seconds=600),
            cache_key=sha256(f"synthetic:{category.value}".encode()).hexdigest(),
            cached=False,
        )
    return ExternalResearchResult(
        **reports,
        researched_at=NOW,
        contract_fingerprint=contract.fingerprint,
    )


async def _live_research(contract, providers) -> ExternalResearchResult:
    service = ExternalResearchService.from_providers(providers.research, providers.llm)
    return await service.run(
        ExternalResearchInput(
            semantic_contract=contract.to_dict(),
            audience=_audience(contract),
        )
    )


async def _run(live_research: bool) -> None:
    settings = get_settings()
    providers = create_provider_bundle(settings)
    contract = _contract()

    if live_research:
        print("Assembling live research (this runs the full stage)...", flush=True)
        started = time.perf_counter()
        research = await _live_research(contract, providers)
        print(f"  research ready in {time.perf_counter() - started:.0f}s", flush=True)
    else:
        research = _synthetic_research(contract)
        print("Research is synthetic; pass --live-research for the real stage.", flush=True)

    runtime = AgentRuntime(ToolRegistry())
    register_marketing_strategist_agent(runtime, providers.llm)

    print("Deciding strategy, 60-180s, no output until it answers...", flush=True)
    started = time.perf_counter()
    output = await runtime.run(
        MARKETING_STRATEGIST_AGENT_NAME,
        {
            "brief": ClientUnderstandingBrief(
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
            ).model_dump(mode="json"),
            "semantic_contract": contract.to_dict(),
            "brand": _brand(contract).model_dump(mode="json"),
            "product": _product(contract).model_dump(mode="json"),
            "audience": _audience(contract).model_dump(mode="json"),
            "research": research.model_dump(mode="json"),
        },
        invocation=None,
    )
    elapsed = time.perf_counter() - started
    if not isinstance(output, MarketingStrategy):
        raise SystemExit(f"unexpected output type: {type(output).__name__}")

    print(f"\nStrategy decided in {elapsed:.0f}s\n")
    for name in STRATEGY_DECISIONS:
        decision = getattr(output, name)
        print(f"{name}")
        print(f"  decision  {decision.decision}")
        print(f"  because   {decision.rationale}")
        print(f"  principle {decision.principle.value}")
        print(f"  basis     {', '.join(decision.basis)}")
    framework = output.message_framework
    print("message_framework")
    print(f"  choice    {framework.framework.value}")
    print(f"  because   {framework.rationale}")
    print("\nlimitations")
    for limitation in output.limitations:
        print(f"  - {limitation}")
    if "--json" in sys.argv:
        print(json.dumps(output.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print("\nMARKETING STRATEGY VERIFIED")


if __name__ == "__main__":
    asyncio.run(_run("--live-research" in sys.argv))
