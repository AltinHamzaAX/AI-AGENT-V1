import hashlib
import io
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload
from test_generation_planner import _asset

from app.modules.posts.agents.asset_intelligence import IntelligentAssetRole
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.tools.composition import (
    EMBEDDED_FAMILY,
    ComponentKind,
    ComposerInput,
    CompositionError,
    CompositionFailure,
    DeterministicComposer,
    FontLibrary,
    SourceVisual,
    TypographyRenderer,
)
from app.modules.posts.tools.composition.renderers import _line_width


def _font_file(directory: Path, name: str) -> Path:
    """Write the face Pillow embeds to disk so a brand family can be registered."""
    source = ImageFont.load_default(size=12).path
    source.seek(0)
    path = directory / name
    path.write_bytes(source.read())
    return path


@pytest.fixture
def font_dir() -> Iterator[Path]:
    # Deliberately not pytest's tmp_path fixture: its numbered root is a
    # long-lived directory, so one broken ACL on it disables every test that
    # only ever needed a scratch folder.
    with TemporaryDirectory() as directory:
        yield Path(directory)


def _png(
    size: tuple[int, int], color: tuple[int, int, int, int], *, label: str | None = None
) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size[0] - 3, size[1] - 3), radius=12, fill=color)
    if label:
        draw.text((12, size[1] // 2 - 6), label, fill="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _payload() -> ComposerInput:
    design_input = await _design_input()
    fingerprint = design_input.copy_draft.contract_fingerprint
    spec = DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint)
    product_policy = _asset(IntelligentAssetRole.PRIMARY_PRODUCT, fingerprint)
    logo_policy = _asset(IntelligentAssetRole.BRAND_LOGO, fingerprint)
    scene_bytes = _png((1080, 1080), (30, 55, 90, 255))
    product_bytes = _png((600, 360), (210, 80, 25, 255), label="ORIGINAL PRODUCT")
    logo_bytes = _png((360, 100), (20, 20, 20, 255), label="ORIGINAL LOGO")
    return ComposerInput(
        scene=SourceVisual(
            asset_id=uuid4(),
            role=IntelligentAssetRole.ENVIRONMENT,
            image_bytes=scene_bytes,
            mime_type="image/png",
            source_checksum=hashlib.sha256(scene_bytes).hexdigest(),
        ),
        products=[
            SourceVisual(
                asset_id=product_policy.asset_id,
                role=product_policy.role,
                image_bytes=product_bytes,
                mime_type="image/png",
                source_checksum=hashlib.sha256(product_bytes).hexdigest(),
            )
        ],
        logo=SourceVisual(
            asset_id=logo_policy.asset_id,
            role=logo_policy.role,
            image_bytes=logo_bytes,
            mime_type="image/png",
            source_checksum=hashlib.sha256(logo_bytes).hexdigest(),
        ),
        copy_draft=design_input.copy_draft,
        design_spec=spec,
        asset_policies=[product_policy, logo_policy],
        final_scale=2,
    )


@pytest.mark.asyncio
async def test_composer_builds_working_preview_final_and_metadata() -> None:
    payload = await _payload()

    result = DeterministicComposer().compose(payload)

    assert (result.working_render.width, result.working_render.height) == (1080, 1080)
    assert (result.preview.width, result.preview.height) == (720, 720)
    assert (result.final_asset.width, result.final_asset.height) == (2160, 2160)
    assert result.working_render.image_bytes.startswith(b"\x89PNG")
    assert result.preview.image_bytes.startswith(b"\x89PNG")
    assert result.final_asset.image_bytes.startswith(b"\x89PNG")
    kinds = {component.kind for component in result.components}
    assert {
        ComponentKind.SCENE,
        ComponentKind.GRAPHIC_ELEMENT,
        ComponentKind.PRODUCT,
        ComponentKind.TYPOGRAPHY,
        ComponentKind.OFFER,
        ComponentKind.CTA,
        ComponentKind.LOGO,
    }.issubset(kinds)
    assert result.contract_fingerprint == payload.design_spec.contract_fingerprint


@pytest.mark.asyncio
async def test_original_product_and_logo_have_identity_provenance() -> None:
    payload = await _payload()

    result = DeterministicComposer().compose(payload)

    product = next(item for item in result.components if item.kind is ComponentKind.PRODUCT)
    logo = next(item for item in result.components if item.kind is ComponentKind.LOGO)
    assert payload.logo is not None
    assert product.source_asset_id == payload.products[0].asset_id
    assert product.source_checksum == payload.products[0].source_checksum
    assert product.identity_preserved is True
    assert logo.source_asset_id == payload.logo.asset_id
    assert logo.source_checksum == payload.logo.source_checksum
    assert logo.identity_preserved is True


@pytest.mark.asyncio
async def test_copy_offer_and_cta_are_rendered_exactly_from_copy_draft() -> None:
    payload = await _payload()

    result = DeterministicComposer().compose(payload)
    rendered_text = {item.text for item in result.components if item.text is not None}

    assert payload.copy_draft.headline in rendered_text
    assert payload.copy_draft.supporting_copy in rendered_text
    assert payload.copy_draft.offer_copy in rendered_text
    assert payload.copy_draft.cta in rendered_text


@pytest.mark.asyncio
async def test_same_inputs_produce_byte_identical_outputs() -> None:
    payload = await _payload()
    composer = DeterministicComposer()

    first = composer.compose(payload)
    second = composer.compose(payload)

    assert first.working_render.image_bytes == second.working_render.image_bytes
    assert first.preview.image_bytes == second.preview.image_bytes
    assert first.final_asset.image_bytes == second.final_asset.image_bytes
    assert first.render_fingerprint == second.render_fingerprint
    assert first.components == second.components


@pytest.mark.asyncio
async def test_missing_required_logo_fails_before_rendering() -> None:
    payload = await _payload()

    invalid = ComposerInput(
        scene=payload.scene,
        products=payload.products,
        logo=None,
        copy_draft=payload.copy_draft,
        design_spec=payload.design_spec,
        asset_policies=payload.asset_policies,
    )

    with pytest.raises(CompositionError) as failure:
        DeterministicComposer().compose(invalid)

    assert failure.value.failure is CompositionFailure.MISSING_REQUIRED_ASSET


@pytest.mark.asyncio
async def test_tampered_product_checksum_is_a_hard_failure() -> None:
    payload = await _payload()
    tampered = payload.products[0].model_copy(update={"source_checksum": "0" * 64})
    invalid = payload.model_copy(update={"products": [tampered]})

    with pytest.raises(CompositionError) as failure:
        DeterministicComposer().compose(invalid)

    assert failure.value.failure is CompositionFailure.CHECKSUM_MISMATCH


@pytest.mark.asyncio
async def test_mime_spoofing_is_rejected() -> None:
    payload = await _payload()
    spoofed = payload.products[0].model_copy(update={"mime_type": "image/jpeg"})
    invalid = payload.model_copy(update={"products": [spoofed]})

    with pytest.raises(CompositionError) as failure:
        DeterministicComposer().compose(invalid)

    assert failure.value.failure is CompositionFailure.MIME_MISMATCH


@pytest.mark.asyncio
async def test_semantic_contract_drift_is_rejected() -> None:
    payload = await _payload()
    drifted_spec = payload.design_spec.model_copy(update={"contract_fingerprint": "0" * 64})

    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        ComposerInput(
            scene=payload.scene,
            products=payload.products,
            logo=payload.logo,
            copy_draft=payload.copy_draft,
            design_spec=drifted_spec,
            asset_policies=payload.asset_policies,
        )


@pytest.mark.asyncio
async def test_high_resolution_export_limit_fails_closed() -> None:
    from app.modules.posts.tools.composition import ExportRenderer

    canvas = Image.new("RGBA", (2100, 2100), "white")

    with pytest.raises(CompositionError) as failure:
        ExportRenderer().export(canvas, final_scale=4)

    assert failure.value.failure is CompositionFailure.EXPORT_TOO_LARGE


@pytest.mark.asyncio
async def test_rendered_typography_records_the_face_it_actually_used() -> None:
    payload = await _payload()

    result = DeterministicComposer().compose(payload)

    headline = next(
        item for item in result.components if item.component_id == "text-headline"
    )
    assert EMBEDDED_FAMILY in headline.detail
    assert "substituted for brand-display 700" in headline.detail
    assert "synthetic bold" in headline.detail


@pytest.mark.asyncio
async def test_registered_brand_face_is_preferred_over_the_fallback(font_dir: Path) -> None:
    payload = await _payload()
    _font_file(font_dir, "brand-display-700.ttf")
    _font_file(font_dir, "brand-body-400.ttf")

    result = DeterministicComposer(FontLibrary.from_directory(font_dir)).compose(payload)

    headline = next(
        item for item in result.components if item.component_id == "text-headline"
    )
    assert "brand-display 700" in headline.detail
    assert "substituted" not in headline.detail


@pytest.mark.asyncio
async def test_nearest_registered_weight_stands_in_without_changing_family(
    font_dir: Path,
) -> None:
    _font_file(font_dir, "brand-display-400.ttf")
    library = FontLibrary.from_directory(font_dir)

    face = library.load("brand-display", 700, 32)

    assert face.family == "brand-display"
    assert face.weight == 400
    assert face.substituted_family is False
    assert face.synthetic_bold is True


@pytest.mark.asyncio
async def test_strict_library_fails_closed_on_an_unavailable_family() -> None:
    payload = await _payload()
    strict = FontLibrary(fallback_family=None)

    with pytest.raises(CompositionError) as failure:
        DeterministicComposer(strict).compose(payload)

    assert failure.value.failure is CompositionFailure.FONT_UNAVAILABLE
    assert "brand-" in failure.value.detail


@pytest.mark.asyncio
async def test_letter_spacing_is_applied_to_the_rendered_line() -> None:
    face = FontLibrary().load(EMBEDDED_FAMILY, 400, 32)

    tight = _line_width(face, "PROMOTIVA", 0)
    tracked = _line_width(face, "PROMOTIVA", 4)

    assert tracked == pytest.approx(tight + 8 * 4)


@pytest.mark.asyncio
async def test_text_wider_than_its_region_fails_closed() -> None:
    payload = await _payload()
    composer = DeterministicComposer()
    plan = composer.compose(payload).typography_plan
    block = plan.blocks[0]
    squeezed = plan.model_copy(
        update={
            "blocks": [
                block.model_copy(
                    update={"bounds": block.bounds.model_copy(update={"width": 12})}
                ),
                *plan.blocks[1:],
            ]
        }
    )
    canvas = Image.new("RGBA", (1080, 1080), "white")

    with pytest.raises(CompositionError) as failure:
        TypographyRenderer().render(canvas, squeezed, payload.design_spec, z_index=60)

    assert failure.value.failure is CompositionFailure.TEXT_OVERFLOW


@pytest.mark.asyncio
async def test_decompression_bomb_is_rejected_as_an_invalid_image(monkeypatch) -> None:
    payload = await _payload()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 16)

    with pytest.raises(CompositionError) as failure:
        DeterministicComposer().compose(payload)

    assert failure.value.failure is CompositionFailure.INVALID_IMAGE
