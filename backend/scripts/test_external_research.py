"""Run the external research stage against configured providers.

Verifies the whole stage end to end: structured analysis, provider targeting,
evidence depth, visual references, degradation, measurement, and cache reuse
both for a repeated request and for a different post by the same client.
"""

import asyncio
import json
import sys
from pathlib import Path

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_provider_bundle  # noqa: E402
from app.modules.posts.agents.audience_research import AudienceIntelligence  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools.research import (
    ExternalResearchInput,
    ExternalResearchService,
    InMemoryResearchCache,
    InMemoryResearchMetricsSink,
    ResearchCategory,
    VisualReferenceTool,  # noqa: E402
    default_research_tools,
    resolve_country,
    validate_external_research_input,
)


def _contract(
    *,
    goal: str = "Drive bookings",
    offer: str = "From EUR 35/day",
) -> PostSemanticContract:
    return PostSemanticContract.create(
        company="Promotiva Mobility",
        brand="Prishtina Drive",
        product="Airport car rental",
        primary_entity="Airport car rental",
        goal=goal,
        audience="Diaspora arriving in Kosovo",
        market="Kosovo",
        location="Prishtina airport",
        offer=offer,
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


def _payload(contract: PostSemanticContract) -> ExternalResearchInput:
    return ExternalResearchInput(
        semantic_contract=contract.to_dict(),
        audience=_audience(contract),
    )


def _report_summary(report) -> dict:
    excerpts = [len(source.excerpt) for source in report.sources]
    return {
        "status": report.status.value,
        "confidence": report.confidence.value,
        "sources": len(report.sources),
        "longest_excerpt_chars": max(excerpts) if excerpts else 0,
        "visual_references": len(report.visual_references),
        "degraded_dimensions": report.degraded_dimensions,
        "structured_analysis": report.analysis is not None,
        "evidence_coverage": (
            report.evidence_coverage.model_dump(mode="json")
            if report.evidence_coverage is not None
            else None
        ),
        "error": report.error,
        "cached": report.cached,
        "expires_at": report.expires_at.isoformat(),
    }


def _summary(result) -> dict:
    return {
        "contract_fingerprint": result.contract_fingerprint,
        "researched_at": result.researched_at.isoformat(),
        "reports": {
            category.value: _report_summary(getattr(result, category.value))
            for category in ResearchCategory
        },
    }


#: The categories that now run a grounded analysis pass, not just a search.
ANALYZED = ("market", "competitor", "social", "visual_reference", "trend", "platform")
#: Trend lifecycle stages, in the order the engine reports them.
TREND_STAGES = ("current", "emerging", "overused", "declining")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def _print_targeting(context, provider) -> None:
    print("Targeting")
    print(f"  market={context.market!r} location={context.location!r}")
    print(f"  resolved provider country={resolve_country(context)!r}")
    tools = {tool.category: tool for tool in default_research_tools(provider)}
    for category in ResearchCategory:
        tool = tools[category]
        request = tool.build_request(
            context,
            query=tool.build_query(context),
            max_results=3,
        )
        print(
            f"  {category.value:<17} topic={request.topic:<7} "
            f"time_range={str(request.time_range):<5} country={str(request.country):<8} "
            f"pinned_domains={len(request.include_domains)} "
            f"images={str(request.include_images):<5} body={request.include_raw_content}"
        )


async def _run() -> None:
    settings = get_settings()
    providers = create_provider_bundle(settings)
    contract = _contract()
    cache = InMemoryResearchCache()
    metrics = InMemoryResearchMetricsSink()

    def service() -> ExternalResearchService:
        return ExternalResearchService.from_providers(
            providers.research,
            providers.llm,
            cache=cache,
            cache_ttl_seconds=settings.research_cache_ttl_seconds,
            max_concurrency=settings.research_max_concurrency,
            search_timeout_seconds=settings.research_search_timeout_seconds,
            tool_timeout_seconds=settings.research_tool_timeout_seconds,
            stage_timeout_seconds=settings.research_stage_timeout_seconds,
            metrics_sink=metrics,
        )

    _, context = validate_external_research_input(_payload(contract))
    _print_targeting(context, providers.research)

    print("\nRunning 8 external research categories...")
    first = await service().run(_payload(contract))
    print(json.dumps(_summary(first), ensure_ascii=False, indent=2))

    print("\nStage metrics")
    print(json.dumps(metrics.recorded[-1].as_metadata(), indent=2))

    print("\nRepeating the same request to verify cache reuse...")
    second = await service().run(_payload(contract))

    print("Same client, different post (new goal and offer)...")
    other_contract = _contract(goal="Grow winter demand", offer="From EUR 29/day")
    other = await service().run(_payload(other_contract))

    longest_excerpt = max(
        (
            len(source.excerpt)
            for category in ResearchCategory
            for source in getattr(first, category.value).sources
        ),
        default=0,
    )
    failures = [
        f"{category.value}={getattr(first, category.value).error}"
        for category in ResearchCategory
        if getattr(first, category.value).error
    ]

    _visual_plan = VisualReferenceTool(providers.research).build_dimension_queries(context)

    print("\nChecks")
    checks = [
        _check(
            "all six analyzed categories carry structured analysis",
            all(getattr(first, name).analysis is not None for name in ANALYZED),
            ", ".join(name for name in ANALYZED if getattr(first, name).analysis is None)
            or "market, competitor, social, visual reference, trend, platform",
        ),
        _check(
            "fourteen visual attributes cost four searches",
            len(set(_visual_plan.values())) == 4 and len(_visual_plan) == 14,
            f"{len(set(_visual_plan.values()))} searches for {len(_visual_plan)} dimensions",
        ),
        _check(
            "every trend is judged for brand, audience and objective fit",
            all(
                insight.usable == (insight.brand_fit and insight.audience_fit)
                and insight.usable == (insight.usable and insight.objective_fit)
                for stage in TREND_STAGES
                for insight in getattr(first.trend.analysis, stage, [])
            ),
            f"{len(first.trend.analysis.usable)} usable of "
            f"{sum(len(getattr(first.trend.analysis, stage)) for stage in TREND_STAGES)}"
            if first.trend.analysis is not None
            else "no trend analysis",
        ),
        _check(
            "no category failed",
            not failures,
            ", ".join(failures),
        ),
        _check(
            "evidence is deeper than a search snippet",
            longest_excerpt > 1_000,
            f"longest excerpt {longest_excerpt} chars",
        ),
        _check(
            "visual reference research returned images",
            bool(first.visual_reference.visual_references),
            f"{len(first.visual_reference.visual_references)} images",
        ),
        _check(
            "repeat request was fully cached",
            all(getattr(second, category.value).cached for category in ResearchCategory),
        ),
        _check(
            "a different post for the same client reused the research",
            all(getattr(other, category.value).cached for category in ResearchCategory),
            f"fingerprint {first.contract_fingerprint[:8]} -> {other.contract_fingerprint[:8]}",
        ),
        _check(
            "every run was measured",
            len(metrics.recorded) == 3,
            f"{len(metrics.recorded)} stage measurements",
        ),
        _check(
            "timeouts are configured in order",
            settings.research_search_timeout_seconds
            <= settings.research_tool_timeout_seconds
            <= settings.research_stage_timeout_seconds,
            f"{settings.research_search_timeout_seconds}s / "
            f"{settings.research_tool_timeout_seconds}s / "
            f"{settings.research_stage_timeout_seconds}s",
        ),
    ]

    if not all(checks):
        raise RuntimeError("external research verification failed")
    print("\nEXTERNAL RESEARCH VERIFIED")


if __name__ == "__main__":
    asyncio.run(_run())
