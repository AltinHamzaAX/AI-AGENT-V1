"""Manual Ticket 38 Senior Design Critic runner.

From ``backend``::

    uv run python scripts/test_design_critic.py --demo --offline
    uv run python scripts/test_design_critic.py --demo --offline --weak

Remove ``--offline`` to inspect the demo render with the configured vision
provider. Real pipeline outputs may be supplied with ``--state`` and ``--input``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = BACKEND_ROOT / "tests"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.integrations.provider_factory import create_vision_provider  # noqa: E402
from app.modules.posts.agents.art_director import ArtDirection  # noqa: E402
from app.modules.posts.agents.design_critic import (  # noqa: E402
    DesignCriticDecision,
    DesignCriticInput,
    DesignDimension,
    SeniorDesignCritic,
)
from app.modules.posts.agents.design_spec import DesignSpec  # noqa: E402
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
        checks = []
        for dimension in DesignDimension:
            failed = self._weak and dimension is DesignDimension.HIERARCHY
            checks.append(
                {
                    "dimension": dimension.value,
                    "passed": not failed,
                    "problem": "Headline and CTA compete for primary attention."
                    if failed
                    else None,
                    "location": "upper-right headline and CTA regions" if failed else None,
                    "cause": "Similar scale and contrast create two simultaneous focal points."
                    if failed
                    else None,
                    "severity": "high" if failed else None,
                    "recommended_change": (
                        "Reduce CTA contrast and preserve the headline as the primary focal point."
                        if failed
                        else None
                    ),
                    "evidence": (
                        "Headline and CTA carry nearly equal visible weight."
                        if failed
                        else "The rendered relationships visibly satisfy this design dimension."
                    ),
                }
            )
        return VisionResponse(
            data={"checks": checks, "summary": "Offline senior design-review exercise."},
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
        "--weak", action="store_true", help="With --offline, simulate a hierarchy defect"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("tmp/design-critic-demo/03-report.json")
    )
    return parser.parse_args()


async def _demo_payload(output_dir: Path) -> DesignCriticInput:
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from test_design_critic import _state  # noqa: PLC0415

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


def _section(state: dict[str, Any], section: PostWorkflowSection) -> dict[str, Any]:
    value = state.get(section.value)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"workflow state requires a populated {section.value} object")
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


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.demo:
            payload = await _demo_payload(Path("tmp/design-critic-demo").resolve())
        else:
            if args.state is None or args.input is None:
                raise ValueError("use --demo, or provide both --state and --input")
            state = _load_state(args.state.resolve())
            image = args.input.resolve().read_bytes()
            draft = PostDraft.model_validate(_section(state, PostWorkflowSection.POST_DRAFT))
            payload = DesignCriticInput(
                final_image=image,
                final_mime_type=_mime_type(args.input),
                semantic_contract=_section(state, PostWorkflowSection.SEMANTIC_CONTRACT),
                art_direction=ArtDirection.model_validate(
                    _section(state, PostWorkflowSection.ART_DIRECTION)
                ),
                design_spec=DesignSpec.model_validate(
                    _section(state, PostWorkflowSection.DESIGN_SPEC)
                ),
                post_draft=draft,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        vision = _OfflineVision(weak=args.weak) if args.offline else create_vision_provider()
        report = await SeniorDesignCritic(vision).review(payload)
    except ProviderError as exc:
        print(f"FAILED CLOSED: {type(exc).__name__}: {exc}")
        return 2

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    print(f"DECISION: {report.decision.value}")
    print(f"Model:    {report.provider}/{report.model}")
    print(f"Report:   {output}")
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.dimension.value:21s} {check.evidence}")
        if not check.passed:
            print(f"         problem:  {check.problem}")
            print(f"         location: {check.location}")
            print(f"         cause:    {check.cause}")
            print(f"         severity: {check.severity.value}")
            print(f"         change:   {check.recommended_change}")
    return 0 if report.decision is DesignCriticDecision.PASS else 1


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
