from copy import deepcopy

import pytest
from test_design_spec import _input as _design_input
from test_design_spec import _spec_payload

from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.tools.design import (
    LayoutEngine,
    LayoutRole,
    TextRole,
    TypographyEngine,
    TypographyHardFailure,
    TypographyInput,
    TypographyLayoutError,
)


async def _input() -> TypographyInput:
    design_input = await _design_input()
    fingerprint = design_input.copy_draft.contract_fingerprint
    spec = DesignSpec(**_spec_payload(), contract_fingerprint=fingerprint)
    return TypographyInput(
        design_spec=spec,
        layout_plan=LayoutEngine().build(spec),
        copy_draft=design_input.copy_draft,
    )


@pytest.mark.asyncio
async def test_typography_engine_returns_render_ready_text_blocks() -> None:
    payload = await _input()
    plan = TypographyEngine().build(payload)

    blocks = {block.role: block for block in plan.blocks}
    assert plan.schema_version == "1.0"
    assert blocks[TextRole.HEADLINE].text == payload.copy_draft.headline
    assert blocks[TextRole.OFFER].text == payload.copy_draft.offer_copy
    assert blocks[TextRole.CTA].text == payload.copy_draft.cta
    assert blocks[TextRole.SUPPORTING].text == payload.copy_draft.supporting_copy
    assert all(block.lines for block in plan.blocks)
    assert all(len(block.lines) <= block.max_lines for block in plan.blocks)
    assert all(block.text_width_px <= block.bounds.width for block in plan.blocks)
    assert all(block.contrast_ratio >= 3 for block in plan.blocks)
    assert plan.overflow is False and plan.hard_failures == []


@pytest.mark.asyncio
async def test_fit_logic_reduces_size_and_preserves_exact_copy() -> None:
    payload = await _input()
    plan = TypographyEngine().build(payload)
    supporting = next(block for block in plan.blocks if block.role is TextRole.SUPPORTING)

    assert supporting.fit_status == "adjusted"
    assert supporting.font_size_px < next(
        role.size_px
        for role in payload.design_spec.typography_roles
        if role.role == "supporting_copy"
    )
    assert supporting.text == payload.copy_draft.supporting_copy


@pytest.mark.asyncio
async def test_tiny_region_is_hard_overflow_failure() -> None:
    payload = await _input()
    placements = [
        item.model_copy(update={"height": 20})
        if item.role is LayoutRole.HEADLINE
        else item
        for item in payload.layout_plan.placements
    ]
    changed = payload.model_copy(
        update={"layout_plan": payload.layout_plan.model_copy(update={"placements": placements})}
    )

    with pytest.raises(TypographyLayoutError) as exc:
        TypographyEngine().build(changed)
    assert exc.value.failure is TypographyHardFailure.OVERFLOW


@pytest.mark.asyncio
async def test_low_contrast_is_hard_unreadable_failure() -> None:
    payload = await _input()
    colors = [
        item.model_copy(update={"value": "#F4F1EA"}) if item.role == "text" else item
        for item in payload.design_spec.color_system
    ]
    changed = payload.model_copy(
        update={"design_spec": payload.design_spec.model_copy(update={"color_system": colors})}
    )

    with pytest.raises(TypographyLayoutError) as exc:
        TypographyEngine().build(changed)
    assert exc.value.failure is TypographyHardFailure.UNREADABLE


@pytest.mark.asyncio
async def test_text_outside_safe_area_is_hard_failure() -> None:
    payload = await _input()
    placements = [
        item.model_copy(update={"x": 0}) if item.role is LayoutRole.CTA else item
        for item in payload.layout_plan.placements
    ]
    changed = payload.model_copy(
        update={"layout_plan": payload.layout_plan.model_copy(update={"placements": placements})}
    )

    with pytest.raises(TypographyLayoutError) as exc:
        TypographyEngine().build(changed)
    assert exc.value.failure is TypographyHardFailure.OUTSIDE_SAFE_AREA


@pytest.mark.asyncio
async def test_unavailable_font_is_hard_failure() -> None:
    payload = await _input()
    with pytest.raises(TypographyLayoutError) as exc:
        TypographyEngine(available_families={"another-font"}).build(payload)
    assert exc.value.failure is TypographyHardFailure.FONT_UNAVAILABLE


@pytest.mark.asyncio
async def test_legal_role_is_supported_when_region_style_and_text_exist() -> None:
    payload = await _input()
    value = deepcopy(_spec_payload())
    value["regions"]["legal_region"] = {"x": 64, "y": 980, "width": 500, "height": 36}
    value["typography_roles"].append(
        {
            "role": "legal", "family_token": "brand-body", "weight": 400,
            "size_px": 12, "line_height": 1.1, "letter_spacing_px": 0,
            "max_lines": 2, "align": "left",
        }
    )
    spec = DesignSpec(**value, contract_fingerprint=payload.design_spec.contract_fingerprint)
    changed = TypographyInput(
        design_spec=spec,
        layout_plan=LayoutEngine().build(spec),
        copy_draft=payload.copy_draft,
        legal_text="Terms apply.",
    )

    result = TypographyEngine().build(changed)

    legal = next(block for block in result.blocks if block.role is TextRole.LEGAL)
    assert legal.text == "Terms apply."
    assert legal.font_size_px == 12


@pytest.mark.asyncio
async def test_semantic_drift_is_rejected_before_layout() -> None:
    payload = await _input()
    drifted = payload.model_copy(
        update={
            "copy_draft": payload.copy_draft.model_copy(
                update={"contract_fingerprint": "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="disagree on the semantic contract"):
        TypographyInput.model_validate(drifted.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_engine_is_deterministic_and_uses_no_image_model() -> None:
    payload = await _input()
    before = payload.model_dump(mode="json")

    first = TypographyEngine().build(payload)
    second = TypographyEngine().build(payload)

    assert first == second
    assert payload.model_dump(mode="json") == before
