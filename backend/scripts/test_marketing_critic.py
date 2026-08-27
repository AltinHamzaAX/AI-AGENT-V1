"""Manual Ticket 37 runner against a real final render and exported workflow state.

From ``backend``::

    uv run python scripts/test_marketing_critic.py --demo --offline

or, against a real pipeline output::

    uv run python scripts/test_marketing_critic.py \
      --state tmp/workflow-state.json --input tmp/final.png --offline

Remove ``--offline`` to use the configured vision provider. The input must be
the exact final asset described by ``post_draft.final_asset.checksum``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
TEST_ROOT = BACKEND_ROOT / "tests"

from app.integrations.provider_factory import create_vision_provider  # noqa: E402
from app.modules.posts.agents.copywriter import CopyDraft  # noqa: E402
from app.modules.posts.agents.marketing_critic import (  # noqa: E402
    MARKETING_PASS_SCORE,
    MarketingCriticAgent,
    MarketingCriticDecision,
    MarketingCriticInput,
    MarketingDimension,
)
from app.modules.posts.agents.marketing_strategist import MarketingStrategy  # noqa: E402
from app.modules.posts.domain.enums import PostWorkflowSection  # noqa: E402
from app.modules.posts.providers import (  # noqa: E402
    ProviderError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.composition import PostDraft  # noqa: E402


class _OfflineVision:
    def __init__(self, *, weak: bool) -> None:
        self._weak = weak

    async def analyze(self, _request: VisionRequest) -> VisionResponse:
        reviews = []
        for dimension in MarketingDimension:
            failed = self._weak and dimension is MarketingDimension.CTA
            reviews.append(
                {
                    "dimension": dimension.value,
                    "score": 5 if failed else MARKETING_PASS_SCORE,
                    "issue": "The CTA does not express the approved next step." if failed else None,
                    "severity": "high" if failed else None,
                    "reason": (
                        "The visible CTA conflicts with the approved CTA intent."
                        if failed
                        else "The visible draft is consistent with the approved context."
                    ),
                    "recommended_action": (
                        "Revise only the CTA to express the approved next step." if failed else None
                    ),
                }
            )
        return VisionResponse(
            data={
                "reviews": reviews,
                "summary": "Offline deterministic marketing-critic exercise.",
            },
            provider="scripted",
            model="offline",
        )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Build a deterministic composed draft fixture; no input paths required",
    )
    parser.add_argument("--state", type=Path, help="Exported workflow-state JSON")
    parser.add_argument("--input", type=Path, help="Exact final render image")
    parser.add_argument("--offline", action="store_true", help="Use a scripted critic response")
    parser.add_argument(
        "--weak",
        action="store_true",
        help="With --offline, simulate a failing CTA and targeted revision",
    )
    parser.add_argument("--output", type=Path, default=Path("tmp/marketing-critic-report.json"))
    return parser.parse_args()


async def _demo_payload(output_dir: Path) -> MarketingCriticInput:
    # Reuse the same complete strategy -> copy -> composition fixture exercised
    # by the test suite. Keeping demo fabrication out of production modules
    # prevents sample marketing decisions from leaking into runtime behavior.
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from test_marketing_critic import _state  # noqa: PLC0415

    state, _, payload = await _state()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "01-final.png").write_bytes(payload.final_image)
    (output_dir / "02-workflow-state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    return payload


def _load_state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]
    if not isinstance(value, dict):
        raise ValueError("workflow state JSON must be an object or contain a data object")
    return value


def _mime_type(path: Path) -> str:
    types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    try:
        return types[path.suffix.casefold()]
    except KeyError as exc:
        raise ValueError("input must be PNG, JPEG or WebP") from exc


def _section(state: dict[str, Any], section: PostWorkflowSection) -> dict[str, Any]:
    value = state.get(section.value)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"workflow state requires a populated {section.value} object")
    return value


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.demo:
            payload = await _demo_payload(Path("tmp/marketing-critic-demo").resolve())
        else:
            if args.state is None or args.input is None:
                raise ValueError("use --demo, or provide both --state and --input")
            state = _load_state(args.state.resolve())
            image = args.input.resolve().read_bytes()
            draft = PostDraft.model_validate(_section(state, PostWorkflowSection.POST_DRAFT))
            payload = MarketingCriticInput(
                final_image=image,
                final_mime_type=_mime_type(args.input),
                semantic_contract=_section(state, PostWorkflowSection.SEMANTIC_CONTRACT),
                strategy=MarketingStrategy.model_validate(
                    _section(state, PostWorkflowSection.MARKETING_STRATEGY)
                ),
                copy_draft=CopyDraft.model_validate(_section(state, PostWorkflowSection.COPY)),
                post_draft=draft,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    vision = _OfflineVision(weak=args.weak) if args.offline else create_vision_provider()
    try:
        report = await MarketingCriticAgent(vision).review(payload)
    except ProviderError as exc:
        print(f"FAILED CLOSED: {type(exc).__name__}: {exc}")
        return 2

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    print(f"DECISION: {report.decision.value}")
    print(f"Score:    {report.score}/10")
    print(f"Model:    {report.provider}/{report.model}")
    print(f"Report:   {output}")
    for review in report.reviews:
        mark = "PASS" if review.score >= MARKETING_PASS_SCORE else "FAIL"
        print(f"  [{mark}] {review.dimension.value:23s} {review.score}/10 - {review.reason}")
        if review.recommended_action:
            print(f"         action: {review.recommended_action}")
    return 0 if report.decision is MarketingCriticDecision.PASS else 1


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
