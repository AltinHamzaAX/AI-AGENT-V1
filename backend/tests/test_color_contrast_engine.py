import pytest
from test_typography_engine import _input as _typography_input

from app.modules.posts.tools.design import (
    ColorContrastEngine,
    ColorEngineError,
    ColorEngineInput,
    ColorHardFailure,
    GradientRequest,
    TypographyEngine,
)


async def _input(**updates) -> ColorEngineInput:
    typography_input = await _typography_input()
    values = {
        "design_spec": typography_input.design_spec,
        "typography_plan": TypographyEngine().build(typography_input),
        "approved_brand_palette": ["#D98A2B"],
        "product_colors": ["#2B2B2B", "#555555"],
        "objective": "Increase airport car bookings",
        "mood": "Welcoming confident movement",
    }
    values.update(updates)
    return ColorEngineInput(**values)


@pytest.mark.asyncio
async def test_color_engine_returns_complete_role_based_plan() -> None:
    payload = await _input()
    result = ColorContrastEngine().build(payload)

    assert result.schema_version == "1.0"
    assert result.brand_palette == ["#D98A2B"]
    assert result.dominant == "#F4F1EA"
    assert result.secondary == "#171717"
    assert result.accent == "#D98A2B"
    assert result.background == "#F4F1EA"
    assert result.text_color == "#171717"
    assert result.cta_background == "#171717"
    assert result.cta_text == "#FFFFFF"
    assert all(check.passed for check in result.contrast_checks)
    assert result.product_separation.passed
    assert result.harmony_score == 1
    assert result.gradient is None
    assert result.hard_failures == []
    assert result.contract_fingerprint == payload.design_spec.contract_fingerprint


@pytest.mark.asyncio
async def test_unapproved_brand_color_is_hard_failure() -> None:
    payload = await _input(approved_brand_palette=["#0055AA"])
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(payload)
    assert exc.value.failure is ColorHardFailure.UNAPPROVED_BRAND_COLOR


@pytest.mark.asyncio
async def test_chromatic_color_cannot_be_disguised_as_neutral() -> None:
    payload = await _input()
    colors = [
        item.model_copy(update={"value": "#FF0000"})
        if item.role == "background"
        else item
        for item in payload.design_spec.color_system
    ]
    changed = payload.model_copy(
        update={"design_spec": payload.design_spec.model_copy(update={"color_system": colors})}
    )
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(changed)
    assert exc.value.failure is ColorHardFailure.INVALID_NEUTRAL


@pytest.mark.asyncio
async def test_insufficient_text_contrast_is_hard_failure() -> None:
    payload = await _input()
    colors = [
        item.model_copy(update={"value": "#F4F1EA"}) if item.role == "text" else item
        for item in payload.design_spec.color_system
    ]
    changed = payload.model_copy(
        update={"design_spec": payload.design_spec.model_copy(update={"color_system": colors})}
    )
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(changed)
    assert exc.value.failure is ColorHardFailure.TEXT_CONTRAST


@pytest.mark.asyncio
async def test_product_background_collision_is_hard_failure() -> None:
    payload = await _input(product_colors=["#F4F1EA"])
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(payload)
    assert exc.value.failure is ColorHardFailure.PRODUCT_SEPARATION


@pytest.mark.asyncio
async def test_random_modern_gradient_is_rejected() -> None:
    payload = await _input(
        gradient=GradientRequest(
            colors=["#D98A2B", "#F4F1EA"],
            angle_degrees=45,
            approved=False,
            reason="It looks modern.",
        )
    )
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(payload)
    assert exc.value.failure is ColorHardFailure.RANDOM_GRADIENT


@pytest.mark.asyncio
async def test_approved_objective_grounded_gradient_is_allowed() -> None:
    payload = await _input(
        gradient=GradientRequest(
            colors=["#D98A2B", "#F4F1EA"],
            angle_degrees=90,
            approved=True,
            reason="Supports the welcoming mood with a restrained transition.",
        )
    )
    result = ColorContrastEngine().build(payload)
    assert result.gradient is not None
    assert result.gradient.colors == ["#D98A2B", "#F4F1EA"]


@pytest.mark.asyncio
async def test_typography_color_contract_drift_is_rejected() -> None:
    payload = await _input()
    blocks = [
        item.model_copy(update={"contrast_ratio": 4.5})
        if item.role == "headline"
        else item
        for item in payload.typography_plan.blocks
    ]
    changed = payload.model_copy(
        update={
            "typography_plan": payload.typography_plan.model_copy(update={"blocks": blocks})
        }
    )
    with pytest.raises(ColorEngineError) as exc:
        ColorContrastEngine().build(changed)
    assert exc.value.failure is ColorHardFailure.CONTRACT_DRIFT


@pytest.mark.asyncio
async def test_semantic_drift_is_rejected_before_engine() -> None:
    payload = await _input()
    drifted = payload.model_copy(
        update={
            "typography_plan": payload.typography_plan.model_copy(
                update={"contract_fingerprint": "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        ColorEngineInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_engine_is_deterministic_and_does_not_mutate_inputs() -> None:
    payload = await _input()
    before = payload.model_dump(mode="json")
    first = ColorContrastEngine().build(payload)
    second = ColorContrastEngine().build(payload)
    assert first == second
    assert payload.model_dump(mode="json") == before
