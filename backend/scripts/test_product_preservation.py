"""Manual visual smoke test for Ticket 34 product preservation.

Run without arguments for a generated product-card fixture, or pass a real
customer image with ``--input``. Outputs are written under the ignored ``tmp``
directory so the source and processed PNG can be inspected side by side.
"""

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.posts.agents.asset_intelligence import (  # noqa: E402
    AssetPolicy,
    IntelligentAssetRole,
)
from app.modules.posts.agents.asset_intelligence.policy import (  # noqa: E402
    evaluate_asset_usage,
)
from app.modules.posts.agents.design_spec import Bounds, Canvas  # noqa: E402
from app.modules.posts.tools.assets import (  # noqa: E402
    PreservationError,
    PreservationInput,
    ProductPreservationPipeline,
)

ASSET_ID = UUID("34343434-3434-4434-8434-343434343434")
FINGERPRINT = hashlib.sha256(b"ticket-34-manual-test").hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Optional real PNG/JPEG/WebP product image")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/product-preservation-manual"),
    )
    parser.add_argument("--keep-background", action="store_true")
    parser.add_argument("--crop", action="store_true")
    parser.add_argument("--no-shadow", action="store_true")
    parser.add_argument("--lighting", type=float, default=1.0)
    parser.add_argument(
        "--attempt-replacement",
        action="store_true",
        help="Prove that preserve_identity blocks a requested replacement",
    )
    return parser.parse_args()


def _sample() -> bytes:
    image = Image.new("RGB", (900, 650), "#EFECE5")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((170, 135, 730, 535), radius=55, fill="#C5282F")
    draw.rounded_rectangle((220, 190, 680, 315), radius=18, fill="#181A1F")
    draw.rectangle((260, 350, 640, 450), fill="#F6E6C8")
    draw.text((355, 382), "REAL PRODUCT", fill="#181A1F")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _load_source(path: Path | None) -> tuple[bytes, str]:
    if path is None:
        return _sample(), "image/png"
    if not path.is_file():
        raise ValueError(
            f"Input image does not exist: {path}. Replace the example path with "
            "the real path to your PNG, JPEG, or WebP file."
        )
    image_bytes = path.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as image:
        mime_type = Image.MIME.get(image.format or "")
    if mime_type is None:
        raise ValueError("Input must be a Pillow-supported image with a known MIME type")
    return image_bytes, mime_type


def main() -> int:
    args = _arguments()
    try:
        source_bytes, mime_type = _load_source(args.input)
    except (OSError, ValueError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    policy = AssetPolicy(
        asset_id=ASSET_ID,
        original_filename=args.input.name if args.input else "manual-product.png",
        role=IntelligentAssetRole.PRIMARY_PRODUCT,
        required=True,
        preserve_identity=True,
        allow_crop=args.crop,
        allow_replace=False,
        allow_generation=False,
        min_dominance=0.05,
        max_dominance=0.8,
        classification_reason="Ticket 34 manual visual test",
        contract_fingerprint=FINGERPRINT,
    )
    payload = PreservationInput(
        asset_id=ASSET_ID,
        image_bytes=source_bytes,
        mime_type=mime_type,
        policy=policy,
        canvas=Canvas(width=1080, height=1080),
        target_bounds=Bounds(x=140, y=190, width=800, height=700),
        remove_background=not args.keep_background,
        crop_to_content=args.crop,
        lighting_factor=args.lighting,
        shadow={"enabled": not args.no_shadow},
        replacement_asset_id=uuid4() if args.attempt_replacement else None,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = ProductPreservationPipeline().process(payload)
    except PreservationError as exc:
        print(f"FAILED [{exc.failure.value}]: {exc.detail}")
        return 1

    validation = evaluate_asset_usage([policy], [result.usage_assertion()])
    source_path = output_dir / "01-source.png"
    output_path = output_dir / "02-preserved-output.png"
    report_path = output_dir / "03-report.json"
    with Image.open(io.BytesIO(source_bytes)) as source:
        source.convert("RGBA").save(source_path)
    output_path.write_bytes(result.image_bytes)
    report = {
        "result": result.model_dump(mode="json", exclude={"image_bytes"}),
        "policy_validation": validation.model_dump(mode="json"),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("PASS" if validation.valid else "HARD FAIL")
    print(f"Source: {source_path}")
    print(f"Output: {output_path}")
    print(f"Report: {report_path}")
    print(f"Identity preserved: {result.identity_preserved}")
    print(f"Dominance: {result.dominance:.4f}")
    return 0 if validation.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
