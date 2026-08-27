"""Manual test for Ticket 36 scene purity.

Runs a generated plate through the configured vision model and prints the
PASS / REGENERATE_SCENE verdict with all nine contamination checks. Use
``--contaminated`` for a plate deliberately carrying text, a fake logo, a
watermark and UI chrome, ``--input`` for a real image, or ``--offline`` to
exercise the deterministic policy without calling a model at all.

Host runs need the local Ollama URL, because .env points at the Docker host:

    OLLAMA_BASE_URL=http://localhost:11434 .venv/Scripts/python.exe \\
        scripts/test_scene_purity.py --contaminated
"""

import argparse
import asyncio
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_marketing_strategist import _contract  # noqa: E402

from app.integrations.provider_factory import create_vision_provider  # noqa: E402
from app.modules.posts.agents.asset_intelligence import (  # noqa: E402
    IntelligentAssetRole,
)
from app.modules.posts.providers import (  # noqa: E402
    ProviderError,
    VisionRequest,
    VisionResponse,
)
from app.modules.posts.tools.generation import (  # noqa: E402
    AssetCategory,
    AssetInventory,
    GenerationDecision,
    GenerationKind,
    GenerationPlan,
    GenerationTask,
    PreserveDirective,
)
from app.modules.posts.tools.scene_purity import (  # noqa: E402
    ContaminationKind,
    ScenePurityInput,
    ScenePurityInspector,
    ScenePurityVerdict,
)

PRODUCT_ASSET_ID = UUID("36363636-3636-4636-8636-363636363636")
LOGO_ASSET_ID = UUID("36363636-3636-4636-8636-363636363637")
SCENE_KEY = "posts/manual/scene-purity-plate.png"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Real PNG/JPEG/WebP plate to inspect")
    parser.add_argument(
        "--contaminated",
        action="store_true",
        help="Generate a plate carrying text, a fake logo, a watermark and UI chrome",
    )
    parser.add_argument(
        "--protected",
        action="store_true",
        help="Treat the product as an approved original, so the plate must be product-free",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the vision model and drive the policy with a scripted readout",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/scene-purity-manual"))
    return parser.parse_args()


def _clean_plate() -> bytes:
    """An empty forecourt at dawn: scenery only, exactly what a plate should be."""
    image = Image.new("RGB", (1080, 1080), "#1B2C44")
    draw = ImageDraw.Draw(image)
    for index in range(28):
        shade = 27 + index * 4
        draw.rectangle((0, index * 14, 1080, index * 14 + 14), fill=(shade, shade + 12, shade + 28))
    draw.rectangle((0, 700, 1080, 1080), fill="#2E2A26")
    draw.polygon([(120, 1080), (430, 700), (650, 700), (960, 1080)], fill="#3A3733")
    draw.ellipse((760, 120, 940, 300), fill="#F2C98A")
    return _encode(image)


def _contaminated_plate() -> bytes:
    """The same plate after the generator invented things it was told to omit."""
    image = Image.open(io.BytesIO(_clean_plate())).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 120, 700, 250), fill="#C5282F")
    draw.text((130, 175), "SUMMER SALE -40%", fill="#FFFFFF")
    draw.text((130, 215), "BOOK NOW AT RENTACAR.EXAMPLE", fill="#FFE9A8")
    draw.ellipse((820, 820, 1000, 1000), fill="#FFFFFF")
    draw.text((855, 900), "AVIS", fill="#C5282F")
    draw.text((300, 540), "stock-photo-preview.example", fill="#9AA3B0")
    draw.rectangle((0, 0, 1080, 60), fill="#101418")
    draw.ellipse((18, 20, 38, 40), fill="#FF5F57")
    draw.ellipse((48, 20, 68, 40), fill="#FEBC2E")
    draw.text((520, 30), "https://", fill="#8A93A0")
    return _encode(image)


def _encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _load(path: Path | None, *, contaminated: bool) -> tuple[bytes, str]:
    if path is None:
        return (_contaminated_plate() if contaminated else _clean_plate()), "image/png"
    if not path.is_file():
        raise ValueError(f"Input image does not exist: {path}")
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        mime_type = Image.MIME.get(image.format or "")
    if mime_type is None:
        raise ValueError("Input must be a Pillow-supported image with a known MIME type")
    return data, mime_type


def _plan(fingerprint: str, *, protected: bool) -> GenerationPlan:
    if protected:
        return GenerationPlan(
            decision=GenerationDecision.GENERATE_BACKGROUND,
            inventory=AssetInventory(
                has_logo=True,
                has_product=True,
                has_background=False,
                has_useful_visual=False,
                asset_ids=[PRODUCT_ASSET_ID, LOGO_ASSET_ID],
                roles=[IntelligentAssetRole.PRIMARY_PRODUCT, IntelligentAssetRole.BRAND_LOGO],
            ),
            available=[AssetCategory.PRODUCT, AssetCategory.LOGO],
            missing=[AssetCategory.BACKGROUND],
            preserve=[
                PreserveDirective(
                    asset_id=PRODUCT_ASSET_ID,
                    role=IntelligentAssetRole.PRIMARY_PRODUCT,
                    preserve_identity=True,
                    allow_crop=False,
                    reason="Approved original owns the product region",
                ),
                PreserveDirective(
                    asset_id=LOGO_ASSET_ID,
                    role=IntelligentAssetRole.BRAND_LOGO,
                    preserve_identity=True,
                    allow_crop=False,
                    reason="Approved original owns the logo region",
                ),
            ],
            may_generate=[GenerationKind.BACKGROUND],
            task=GenerationTask(
                kind=GenerationKind.BACKGROUND,
                allowed_content=["environment only"],
                prohibited_content=[
                    "text",
                    "logo",
                    "brand",
                    "price",
                    "call to action",
                    "watermark",
                ],
                preserve_asset_ids=[PRODUCT_ASSET_ID, LOGO_ASSET_ID],
            ),
            estimated_image_calls=1,
            cost_tier="low",
            reason="Ticket 36 manual test with protected originals",
            contract_fingerprint=fingerprint,
        )
    return GenerationPlan(
        decision=GenerationDecision.GENERATE_SCENE,
        inventory=AssetInventory(
            has_logo=False,
            has_product=False,
            has_background=False,
            has_useful_visual=False,
            asset_ids=[],
            roles=[],
        ),
        available=[],
        missing=[AssetCategory.PRODUCT, AssetCategory.LOGO, AssetCategory.BACKGROUND],
        preserve=[],
        may_generate=[GenerationKind.SCENE],
        task=GenerationTask(
            kind=GenerationKind.SCENE,
            allowed_content=["full scene"],
            prohibited_content=[
                "text",
                "logo",
                "brand",
                "price",
                "call to action",
                "watermark",
            ],
            preserve_asset_ids=[],
        ),
        estimated_image_calls=1,
        cost_tier="low",
        reason="Ticket 36 manual test without approved originals",
        contract_fingerprint=fingerprint,
    )


class _ScriptedVision:
    """Stands in for the model so --offline exercises only the policy."""

    def __init__(self, *, contaminated: bool) -> None:
        self.contaminated = contaminated

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        del request
        reported = {
            ContaminationKind.WATERMARK: 0.82,
            ContaminationKind.UNWANTED_UI: 0.77,
        }
        data: dict[str, Any] = {
            "observations": [
                {
                    "kind": kind.value,
                    "confidence": reported.get(kind, 0.0) if self.contaminated else 0.0,
                    "evidence": f"scripted offline readout for {kind.value}",
                }
                for kind in ContaminationKind
            ],
            "visible_text": (
                ["SUMMER SALE -40%", "BOOK NOW AT RENTACAR.EXAMPLE", "https://"]
                if self.contaminated
                else []
            ),
            "visible_brands": ["AVIS"] if self.contaminated else [],
            "depicted_products": ["a silver rental sedan"] if self.contaminated else [],
            "description": "Scripted offline readout for the deterministic policy.",
        }
        return VisionResponse(data=data, provider="scripted", model="offline")


async def _run(args: argparse.Namespace) -> int:
    try:
        image_bytes, mime_type = _load(args.input, contaminated=args.contaminated)
    except (OSError, ValueError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    contract = _contract()
    payload = ScenePurityInput(
        scene_image=image_bytes,
        scene_mime_type=mime_type,
        scene_checksum=hashlib.sha256(image_bytes).hexdigest(),
        scene_storage_key=SCENE_KEY,
        semantic_contract=contract.to_dict(),
        generation_plan=_plan(contract.fingerprint, protected=args.protected),
    )

    vision = _ScriptedVision(contaminated=args.contaminated) if args.offline else None
    if vision is None:
        try:
            vision = create_vision_provider()
        except ProviderError as exc:
            print(f"PROVIDER ERROR: {exc}", file=sys.stderr)
            return 2

    started = time.monotonic()
    try:
        report = await ScenePurityInspector(vision).inspect(payload)
    except ProviderError as exc:
        print(f"FAILED CLOSED: {type(exc).__name__}: {exc}")
        print("The plate is not certified, so composition would refuse it.")
        return 1
    elapsed = time.monotonic() - started

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plate_path = output_dir / "01-plate.png"
    report_path = output_dir / "02-report.json"
    plate_path.write_bytes(image_bytes)
    report_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")

    print(f"VERDICT: {report.verdict.value}")
    print(f"Model:   {report.provider}/{report.model}  ({elapsed:.1f}s)")
    print(f"Plate:   {plate_path}")
    print(f"Report:  {report_path}")
    print()
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.kind.value:17s} {check.detail}")
    if report.findings:
        print()
        print("Composition would refuse this plate and production would run again.")
    return 0 if report.verdict is ScenePurityVerdict.PASS else 1


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
