"""Ticket 34: the customer's product survives the edit.

Everything here exists because the alternative is shipping a picture of a car
the customer does not own. The pipeline may move, scale, light and ground an
uploaded asset; the moment a request asks for a different object in its place,
the answer has to be no - and no is checked here for every protected role, not
only for the flag that happens to be named after it.
"""

import io
from uuid import UUID, uuid4

import pytest
from PIL import Image

from app.modules.posts.agents.asset_intelligence import (
    AssetPolicy,
    IntelligentAssetRole,
)
from app.modules.posts.agents.asset_intelligence.policy import evaluate_asset_usage
from app.modules.posts.agents.design_spec import Bounds, Canvas
from app.modules.posts.tools.assets.preservation import (
    PROTECTED_ROLES,
    BackgroundRemovalTool,
    EditOperation,
    PerspectiveDirective,
    PerspectiveMode,
    PreservationError,
    PreservationFailure,
    PreservationInput,
    ProductPreservationPipeline,
    ShadowDirective,
)

_FINGERPRINT = "f" * 64
_CANVAS = Canvas(width=1080, height=1080)
_TARGET = Bounds(x=140, y=200, width=800, height=600)


def _png(
    *,
    size: tuple[int, int] = (400, 300),
    background: tuple[int, int, int] = (255, 255, 255),
    subject: tuple[int, int, int] = (200, 30, 30),
    inner: tuple[int, int, int] | None = None,
) -> bytes:
    """A subject on a plain backdrop, the shape a phone photo of a product has."""
    image = Image.new("RGB", size, background)
    for x in range(80, size[0] - 80):
        for y in range(60, size[1] - 60):
            image.putpixel((x, y), subject)
    if inner is not None:
        for x in range(150, 200):
            for y in range(120, 160):
                image.putpixel((x, y), inner)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _mask(size: tuple[int, int], *, keep: bool = True) -> bytes:
    mask = Image.new("L", size, 255 if keep else 0)
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    return buffer.getvalue()


def _policy(
    role: IntelligentAssetRole = IntelligentAssetRole.PRIMARY_PRODUCT,
    *,
    asset_id: UUID | None = None,
    preserve_identity: bool = True,
    allow_crop: bool = False,
    allow_replace: bool = False,
    allow_generation: bool = False,
) -> AssetPolicy:
    return AssetPolicy(
        asset_id=asset_id or uuid4(),
        original_filename=f"{role.value}.png",
        role=role,
        required=True,
        preserve_identity=preserve_identity,
        allow_crop=allow_crop,
        allow_replace=allow_replace,
        allow_generation=allow_generation,
        min_dominance=0.1,
        max_dominance=0.8,
        classification_reason="test fixture",
        contract_fingerprint=_FINGERPRINT,
    )


def _request(policy: AssetPolicy, **overrides: object) -> PreservationInput:
    values: dict[str, object] = {
        "asset_id": policy.asset_id,
        "image_bytes": _png(),
        "mime_type": "image/png",
        "policy": policy,
        "canvas": _CANVAS,
        "target_bounds": _TARGET,
    }
    values.update(overrides)
    return PreservationInput(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Replacement is the thing this ticket forbids
# --------------------------------------------------------------------------


def test_an_identity_preserved_asset_cannot_be_swapped_for_another() -> None:
    payload = _request(_policy(), replacement_asset_id=uuid4())

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.REPLACEMENT_FORBIDDEN


def test_an_identity_preserved_asset_cannot_be_stood_in_for_by_generated_bytes() -> None:
    payload = _request(_policy(), source_is_generated=True)

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.GENERATED_SOURCE_FORBIDDEN


@pytest.mark.parametrize("role", sorted(PROTECTED_ROLES))
def test_protected_roles_are_unswappable_even_without_the_preserve_flag(
    role: IntelligentAssetRole,
) -> None:
    """A vehicle nobody authorised for replacement is not replaceable.

    `preserve_identity` being false is not permission; the policy still has to
    say `allow_replace` before another object may stand in.
    """
    payload = _request(
        _policy(role, preserve_identity=False),
        replacement_asset_id=uuid4(),
    )

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.REPLACEMENT_FORBIDDEN


def test_an_explicitly_replaceable_asset_is_routed_outside_preservation() -> None:
    """Permission must not make this pipeline silently ignore a replacement."""
    policy = _policy(
        IntelligentAssetRole.SUPPORTING_ASSET,
        preserve_identity=False,
        allow_replace=True,
    )

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(_request(policy, replacement_asset_id=uuid4()))

    assert failure.value.failure is PreservationFailure.REPLACEMENT_OUT_OF_SCOPE


def test_refusal_happens_before_any_image_is_decoded() -> None:
    """Nothing is opened to answer a question about permission."""
    payload = _request(
        _policy(),
        image_bytes=b"not an image at all",
        replacement_asset_id=uuid4(),
    )

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.REPLACEMENT_FORBIDDEN


def test_the_result_reports_its_usage_for_the_asset_policy_validator() -> None:
    policy = _policy(IntelligentAssetRole.VEHICLE)

    result = ProductPreservationPipeline().process(_request(policy))
    validation = evaluate_asset_usage([policy], [result.usage_assertion()])

    assert result.identity_preserved is True
    assert validation.valid is True
    assert validation.decision == "CONTINUE"
    assert validation.violations == []


def test_fidelity_is_derived_from_the_work_performed_not_the_policy_flag() -> None:
    policy = _policy(IntelligentAssetRole.SUPPORTING_ASSET, preserve_identity=False)

    result = ProductPreservationPipeline().process(_request(policy))

    assert result.identity_preserved is True
    assert "source_checksum_verified" in result.fidelity_evidence
    assert "source_pixels_transformed_without_subject_substitution" in result.fidelity_evidence


# --------------------------------------------------------------------------
# The nine tools
# --------------------------------------------------------------------------


def test_every_tool_reports_once_and_the_output_fills_the_canvas() -> None:
    result = ProductPreservationPipeline().process(_request(_policy()))

    assert [step.operation for step in result.steps] == list(EditOperation)
    assert (result.width, result.height) == (_CANVAS.width, _CANVAS.height)
    assert result.mime_type == "image/png"
    assert result.source_checksum != result.output_checksum
    assert result.contract_fingerprint == _FINGERPRINT


def test_scaling_fits_the_region_without_stretching_the_product() -> None:
    """Aspect ratio is part of identity: a squashed car is a different car."""
    result = ProductPreservationPipeline().process(
        _request(_policy(), image_bytes=_png(size=(400, 200)))
    )

    bounds = result.actual_bounds
    assert bounds.width <= _TARGET.width and bounds.height <= _TARGET.height
    assert abs(bounds.width / bounds.height - 2.0) < 0.02
    assert bounds.x >= _TARGET.x and bounds.y >= _TARGET.y


def test_background_removal_keeps_colour_the_product_encloses() -> None:
    """A window inside a car is not background; punching it out edits the car."""
    payload = _request(_policy(), image_bytes=_png(inner=(255, 255, 255)))

    result = ProductPreservationPipeline().process(payload)

    with Image.open(io.BytesIO(result.image_bytes)) as output:
        alpha = output.convert("RGBA").getchannel("A")
        scale = result.actual_bounds.width / 400
        inside = (
            result.actual_bounds.x + int(175 * scale),
            result.actual_bounds.y + int(140 * scale),
        )
        assert alpha.getpixel(inside) > 0


def test_a_mask_may_hide_more_of_the_asset_but_never_reveal_more() -> None:
    payload = _request(_policy(), mask_bytes=_mask((400, 300), keep=False))

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.INVALID_IMAGE


def test_background_removal_does_not_confuse_dark_product_pixels_with_its_marker() -> None:
    source = Image.open(io.BytesIO(_png(inner=(1, 2, 3))))

    result = BackgroundRemovalTool().apply(source)

    assert result.getpixel((175, 140)) == (1, 2, 3, 255)
    assert result.getpixel((0, 0))[3] == 0


def test_complex_gradient_background_is_refused_instead_of_damaging_product() -> None:
    image = Image.new("RGB", (400, 300), "white")
    image.putpixel((0, 0), (40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    with pytest.raises(PreservationError) as failure:
        BackgroundRemovalTool().apply(Image.open(io.BytesIO(buffer.getvalue())))

    assert failure.value.failure is PreservationFailure.BACKGROUND_REMOVAL_UNSAFE


def test_cropping_requires_permission_and_is_reported_when_taken() -> None:
    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(_request(_policy(), crop_to_content=True))
    assert failure.value.failure is PreservationFailure.CROP_FORBIDDEN

    allowed = _policy(IntelligentAssetRole.SUPPORTING_ASSET, allow_crop=True)
    result = ProductPreservationPipeline().process(_request(allowed, crop_to_content=True))

    assert result.cropped is True
    assert result.usage_assertion().cropped is True


def test_preserving_the_source_perspective_changes_nothing() -> None:
    pipeline = ProductPreservationPipeline()
    policy = _policy()

    untouched = pipeline.process(_request(policy))
    explicit = pipeline.process(
        _request(
            policy,
            perspective=PerspectiveDirective(mode=PerspectiveMode.PRESERVE_SOURCE),
        )
    )

    assert untouched.output_checksum == explicit.output_checksum


def test_a_shadow_is_laid_outside_the_asset_not_over_it() -> None:
    pipeline = ProductPreservationPipeline()
    policy = _policy()

    without = pipeline.process(_request(policy, shadow=ShadowDirective(enabled=False)))
    with_shadow = pipeline.process(_request(policy))

    assert without.output_checksum != with_shadow.output_checksum
    with Image.open(io.BytesIO(with_shadow.image_bytes)) as output:
        rgba = output.convert("RGBA")
        centre = (
            with_shadow.actual_bounds.x + with_shadow.actual_bounds.width // 2,
            with_shadow.actual_bounds.y + with_shadow.actual_bounds.height // 2,
        )
        red, _, _, alpha = rgba.getpixel(centre)
        assert alpha == 255 and red > 150


# --------------------------------------------------------------------------
# Refusals that are not about identity
# --------------------------------------------------------------------------


def test_bounds_outside_the_canvas_are_refused() -> None:
    payload = _request(
        _policy(),
        target_bounds=Bounds(x=900, y=900, width=400, height=400),
    )

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.OUTSIDE_CANVAS


def test_unreadable_asset_bytes_are_refused() -> None:
    payload = _request(_policy(), image_bytes=b"\x89PNG\r\n\x1a\n truncated")

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.INVALID_IMAGE


def test_declared_mime_type_must_match_the_decoded_image() -> None:
    payload = _request(_policy(), mime_type="image/jpeg")

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.MIME_TYPE_MISMATCH


@pytest.mark.parametrize(
    "mask",
    [pytest.param(b"not an image", id="unreadable"), pytest.param(None, id="wrong-size")],
)
def test_a_mask_must_be_a_readable_image_of_the_source_size(mask: bytes | None) -> None:
    mask_bytes = mask if mask is not None else _mask((64, 64))
    payload = _request(_policy(), mask_bytes=mask_bytes)

    with pytest.raises(PreservationError) as failure:
        ProductPreservationPipeline().process(payload)

    assert failure.value.failure is PreservationFailure.INVALID_MASK


def test_the_policy_and_the_request_must_describe_the_same_asset() -> None:
    with pytest.raises(ValueError, match="asset and policy IDs disagree"):
        PreservationInput(
            asset_id=uuid4(),
            image_bytes=_png(),
            mime_type="image/png",
            policy=_policy(),
            canvas=_CANVAS,
            target_bounds=_TARGET,
        )


@pytest.mark.parametrize("factor", [0.5, 1.4])
def test_lighting_cannot_be_pushed_far_enough_to_recolour_the_product(
    factor: float,
) -> None:
    with pytest.raises(ValueError):
        _request(_policy(), lighting_factor=factor)


def test_perspective_correction_stays_within_a_straightening_range() -> None:
    with pytest.raises(ValueError):
        PerspectiveDirective(mode=PerspectiveMode.CORRECT_DISTORTION, strength=0.5)


def test_dominance_measures_the_asset_not_the_box_it_sits_in() -> None:
    """A transparent margin is not the product taking up the frame."""
    result = ProductPreservationPipeline().process(_request(_policy()))

    box = result.actual_bounds
    box_share = (box.width * box.height) / (_CANVAS.width * _CANVAS.height)
    assert 0.13 < result.dominance < 0.17
    assert result.dominance < box_share / 2


def test_an_asset_that_swamps_the_frame_fails_its_policy() -> None:
    policy = _policy(asset_id=uuid4())
    modest = policy.model_copy(update={"max_dominance": 0.05})

    result = ProductPreservationPipeline().process(_request(policy))
    validation = evaluate_asset_usage([modest], [result.usage_assertion()])

    assert validation.decision == "HARD_FAIL"
    assert any("dominance must be between" in item for item in validation.violations)
