"""Compact diagnosis of one structured research category.

Prints a handful of lines, not a transcript: enough to say whether the model
produced a usable analysis and, when it did not, exactly which rule rejected
it. Defaults to market; pass "competitor" or "social" to check another.

Run from backend, with the Ollama host overridden for a non-Docker shell:

    set OLLAMA_BASE_URL=http://localhost:11434
    .venv\\Scripts\\python.exe scripts\\diagnose_research.py
"""

import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.disable(logging.CRITICAL)

from test_external_research import _contract, _payload  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.integrations.ollama import OllamaLLMProvider  # noqa: E402
from app.integrations.provider_factory import create_provider_bundle  # noqa: E402
from app.modules.posts.tools.research import (  # noqa: E402
    CompetitorResearchTool,
    LLMResearchAnalyzer,
    MarketResearchTool,
    PlatformResearchTool,
    SocialResearchTool,
    TrendResearchTool,
    VisualReferenceTool,
    validate_external_research_input,
)
from app.modules.posts.tools.research.analysis import (  # noqa: E402
    _DRAFT_TYPES,
    _analysis_input,
    _system_prompt,
)

#: Stage names in lifecycle order, used to report trend usability.
TREND_STAGES = ("current", "emerging", "overused", "declining")

TOOLS = {
    "market": MarketResearchTool,
    "competitor": CompetitorResearchTool,
    "social": SocialResearchTool,
    "visual": VisualReferenceTool,
    "trend": TrendResearchTool,
    "platform": PlatformResearchTool,
}


def _line(label: str, value: object) -> None:
    # Flushed because the interesting part is a minute of silence in between.
    print(f"{label:<12} {value}", flush=True)


async def _run(name: str) -> None:
    settings = get_settings()
    providers = create_provider_bundle(settings)
    _, context = validate_external_research_input(_payload(_contract()))
    tool_type = TOOLS[name]

    started = time.perf_counter()
    search_only = await tool_type(providers.research).research(
        context, researched_at=datetime.now(UTC), ttl_seconds=600
    )
    _line("category", name)
    _line("sources", f"{len(search_only.sources)} (search {time.perf_counter() - started:.0f}s)")
    plan = tool_type(providers.research).build_dimension_queries(context)
    _line("searches", f"{len(set(plan.values()))} for {len(plan)} dimensions")
    if search_only.visual_references:
        _line("images", len(search_only.visual_references))
    if not search_only.sources:
        _line("verdict", "NO SOURCES - search problem, not the model")
        return

    draft = _DRAFT_TYPES[search_only.category]
    indexed = {f"S{i}": s for i, s in enumerate(search_only.sources[:12], 1)}
    body = json.dumps(_analysis_input(context, indexed), ensure_ascii=False)
    prompt = _system_prompt(search_only.category, draft)
    _line("prompt", f"~{(len(prompt) + len(body)) // 4:,} tokens")
    _line("num_ctx", f"{OllamaLLMProvider._context_window([{'content': prompt + body}]):,}")

    _line("analysing", "calling the model, 60-150s, no output until it answers")
    started = time.perf_counter()
    try:
        report = await asyncio.wait_for(
            tool_type(providers.research, analyzer=LLMResearchAnalyzer(providers.llm)).research(
                context, researched_at=datetime.now(UTC), ttl_seconds=600
            ),
            # A ceiling so this always ends with a verdict rather than a hang.
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - this script exists to report it
        _line("elapsed", f"{time.perf_counter() - started:.0f}s")
        _line("verdict", f"FAILED {type(exc).__name__}")
        cause = exc.__cause__
        depth = 0
        while cause is not None and depth < 3:
            detail = " ".join(str(cause).split())[:220]
            _line(f"cause[{depth}]", f"{type(cause).__name__}: {detail}")
            cause, depth = cause.__cause__, depth + 1
        return

    coverage = report.evidence_coverage
    _line("elapsed", f"{time.perf_counter() - started:.0f}s")
    _line("verdict", "OK")
    _line("coverage", f"{coverage.status.value} ratio={coverage.coverage_ratio:.2f}")
    _line("covered", ", ".join(coverage.covered_dimensions) or "-")
    _line("missing", ", ".join(coverage.missing_dimensions) or "-")
    for dimension in coverage.covered_dimensions[:3]:
        insight = getattr(report.analysis, dimension)[0]
        _line(f"  {dimension}", f"[{insight.confidence.value}] {insight.observation[:90]}")
        _line("  quote", f'"{insight.evidence[0].quote[:80]}"')
    usable = getattr(report.analysis, "usable", None)
    if usable is not None:
        # The whole point of the trend engine: a trend is evidence, and only
        # a trend that fits brand, audience and objective may be acted on.
        total = sum(len(getattr(report.analysis, stage)) for stage in TREND_STAGES)
        _line("usable", f"{len(usable)}/{total} trends passed all three fits")
        for stage in TREND_STAGES:
            for insight in getattr(report.analysis, stage):
                fits = (
                    "".join(
                        mark
                        for mark, ok in (
                            ("B", insight.brand_fit),
                            ("A", insight.audience_fit),
                            ("O", insight.objective_fit),
                        )
                        if ok
                    )
                    or "-"
                )
                verdict = "USE" if insight.usable else "skip"
                _line(f"  {stage}", f"[{verdict}] fits={fits:<3} {insight.observation[:70]}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "market"
    if target not in TOOLS:
        print(f"usage: diagnose_research.py [{'|'.join(TOOLS)}]")
        raise SystemExit(2)
    asyncio.run(_run(target))
