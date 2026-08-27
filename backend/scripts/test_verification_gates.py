"""Manual Ticket 39 Hard Verification Gates runner.

From ``backend``::

    uv run python scripts/test_verification_gates.py --demo --offline
    uv run python scripts/test_verification_gates.py --demo --offline --tamper unwanted_text

Remove ``--offline`` to read the demo render with the configured vision provider.
Exit code 0 means PASS, 1 means BLOCKED, 2 means the gates could not be run.

Expect a live ``--tamper none`` run to come back BLOCKED. The demo composes
placeholder assets with their role printed across them, so the render really
does carry the strings ``primary_product`` and ``brand_logo``, which belong to
no approved copy. That is the gate reading the image correctly, not a false
positive; ``--offline`` scripts a witness that sees only the approved copy and
gives the clean baseline.
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
from app.modules.posts.providers import (  # noqa: E402
    ProviderError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.composition import ComponentKind  # noqa: E402
from app.modules.posts.tools.verification import (  # noqa: E402
    HardVerificationGate,
    VerificationDecision,
    VerificationInput,
)

#: Each tamper injects exactly one contract violation, so the run should end
#: BLOCKED on that gate and only that gate.
TAMPERS = (
    "none",
    "dimensions",
    "asset_fidelity",
    "spelling",
    "forbidden_claim",
    "unwanted_text",
    "foreign_brand",
)


class _OfflineVision:
    """A witness that reports the approved copy, plus whatever the tamper adds."""

    def __init__(self, *, visible_text: list[str], visible_brands: list[str]) -> None:
        self._visible_text = visible_text
        self._visible_brands = visible_brands

    async def analyze(self, _request: VisionRequest) -> VisionResponse:
        return VisionResponse(
            data={
                "visible_text": self._visible_text,
                "visible_brands": self._visible_brands,
                "depicted_products": [],
                "description": "Offline hard-verification exercise.",
            },
            provider="scripted",
            model="offline",
        )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Build a deterministic composed post; no input paths required",
    )
    parser.add_argument("--offline", action="store_true", help="Use a scripted witness")
    parser.add_argument(
        "--tamper",
        choices=TAMPERS,
        default="none",
        help="Inject one contract violation and watch which gate catches it",
    )
    parser.add_argument("--output", type=Path, default=Path("tmp/verification-demo/report.json"))
    return parser.parse_args()


async def _demo_payload(output_dir: Path) -> tuple[VerificationInput, Any]:
    if str(TEST_ROOT) not in sys.path:
        sys.path.insert(0, str(TEST_ROOT))
    from test_verification_gates import _case  # noqa: PLC0415

    case = await _case()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final.png").write_bytes(case.payload.final_image)
    return case.payload, case.storage


def _tamper(
    payload: VerificationInput, choice: str
) -> tuple[VerificationInput, list[str], list[str]]:
    """Return the payload plus what the offline witness should claim to see."""
    copy = payload.copy_draft
    seen = [
        text for text in (copy.headline, copy.cta, copy.offer_copy, copy.supporting_copy) if text
    ]
    brands: list[str] = []
    draft = payload.post_draft

    if choice == "dimensions":
        final = draft.final_asset.model_copy(update={"width": draft.final_asset.width + 7})
        draft = draft.model_copy(update={"final_asset": final})
    elif choice == "asset_fidelity":
        draft = _map_component(draft, ComponentKind.PRODUCT, identity_preserved=False)
    elif choice == "spelling":
        draft = _map_component(draft, ComponentKind.TYPOGRAPHY, text=f"{copy.headline[:-3]}...")
    elif choice == "forbidden_claim":
        claim = payload.contract().forbidden_claims
        copy = copy.model_copy(update={"caption": f"{claim[0] if claim else 'none'} today."})
    elif choice == "unwanted_text":
        seen = [*seen, "MEGA SALE 70% OFF"]
    elif choice == "foreign_brand":
        brands = ["Hertz"]

    updates: dict[str, Any] = {"post_draft": draft, "copy_draft": copy}
    return payload.model_copy(update=updates), seen, brands


def _map_component(draft, kind: ComponentKind, **changes: Any):
    components = [
        component.model_copy(update=changes) if component.kind is kind else component
        for component in draft.components
    ]
    return draft.model_copy(update={"components": components})


async def _run(args: argparse.Namespace) -> int:
    if not args.demo:
        print("INPUT ERROR: --demo is required in this build", file=sys.stderr)
        return 2
    try:
        payload, _ = await _demo_payload(Path("tmp/verification-demo").resolve())
        payload, seen, brands = _tamper(payload, args.tamper)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        vision = (
            _OfflineVision(visible_text=seen, visible_brands=brands)
            if args.offline
            else create_vision_provider()
        )
        report = await HardVerificationGate(vision).verify(payload)
    except ProviderError as exc:
        print(f"COULD NOT VERIFY: {type(exc).__name__}: {exc}")
        return 2

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    print(f"FINAL:    {report.decision.value}")
    print(f"Tamper:   {args.tamper}")
    print(f"Witness:  {report.provider}/{report.model}")
    print(f"Report:   {output}")
    for check in report.checks:
        mark = "PASS" if check.passed else "BLOCK"
        print(f"  [{mark:5s}] {check.gate.value:24s} {check.detail}")
    for failure in report.failures:
        if failure.evidence:
            print(f"  evidence {failure.gate.value}: {failure.evidence}")
    return 0 if report.decision is VerificationDecision.PASS else 1


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
