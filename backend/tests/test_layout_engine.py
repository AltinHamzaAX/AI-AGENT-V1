from copy import deepcopy

import pytest
from test_design_spec import _spec_payload

from app.modules.posts.agents.design_spec import Bounds, DesignSpec
from app.modules.posts.tools.design import (
    Alignment,
    ConstraintKind,
    GridEngine,
    LayoutEngine,
    LayoutPlacement,
    LayoutRole,
    SafeAreaEngine,
    SpacingEngine,
    VisualHierarchyPlanner,
)


def _spec() -> DesignSpec:
    return DesignSpec(**_spec_payload(), contract_fingerprint="a" * 64)


def test_layout_engine_returns_real_coordinates_constraints_and_priorities() -> None:
    plan = LayoutEngine().build(_spec())

    assert plan.schema_version == "1.0"
    assert plan.source_design_spec_version == "1.0"
    assert plan.contract_fingerprint == "a" * 64
    assert [item.role for item in plan.placements] == [
        LayoutRole.PRODUCT,
        LayoutRole.HEADLINE,
        LayoutRole.OFFER,
        LayoutRole.CTA,
        LayoutRole.LOGO,
    ]
    for placement in plan.placements:
        assert placement.x >= 0 and placement.y >= 0
        assert placement.width > 0 and placement.height > 0
        assert placement.priority > 0
        assert {item.kind for item in placement.constraints} >= {
            ConstraintKind.GRID,
            ConstraintKind.MIN_SPACING,
        }


def test_grid_engine_builds_and_snaps_to_real_grid_lines() -> None:
    spec = _spec()
    safe = SafeAreaEngine().bounds(spec)
    geometry = GridEngine().geometry(safe, spec.grid)
    snapped = GridEngine().snap(
        Bounds(x=681, y=97, width=319, height=179),
        geometry,
        container=safe,
    )

    assert len(geometry.x_lines) == spec.grid.columns + 1
    assert len(geometry.y_lines) == spec.grid.rows + 1
    assert snapped.x in geometry.x_lines
    assert snapped.x + snapped.width in geometry.x_lines
    assert snapped.y in geometry.y_lines
    assert snapped.y + snapped.height in geometry.y_lines


def test_safe_area_engine_clamps_text_but_allows_product_canvas_bleed() -> None:
    spec = _spec()
    engine = SafeAreaEngine()
    outside = Bounds(x=0, y=0, width=1080, height=1080)

    text = engine.constrain(outside, spec, allow_bleed=False)
    product = engine.constrain(outside, spec, allow_bleed=True)

    assert text == Bounds(x=64, y=64, width=952, height=952)
    assert product == outside


def test_spacing_engine_enforces_baseline_rhythm() -> None:
    headline = _placement(LayoutRole.HEADLINE, y=100, height=100)
    cta = _placement(LayoutRole.CTA, y=204, height=60)

    with pytest.raises(ValueError, match="below baseline"):
        SpacingEngine().relations([headline, cta], baseline=8)


def test_visual_hierarchy_planner_is_deterministic() -> None:
    planner = VisualHierarchyPlanner()
    roles = [LayoutRole.LOGO, LayoutRole.CTA, LayoutRole.HEADLINE, LayoutRole.PRODUCT]

    priorities = planner.priorities(roles)

    assert priorities[LayoutRole.PRODUCT] > priorities[LayoutRole.HEADLINE]
    assert priorities[LayoutRole.HEADLINE] > priorities[LayoutRole.CTA]
    assert priorities[LayoutRole.CTA] > priorities[LayoutRole.LOGO]
    assert planner.visual_flow(roles) == [
        LayoutRole.PRODUCT,
        LayoutRole.HEADLINE,
        LayoutRole.CTA,
        LayoutRole.LOGO,
    ]


def test_layout_measures_all_requested_design_principles() -> None:
    principles = LayoutEngine().build(_spec()).principles

    assert principles.alignment_score == 1
    assert 0 <= principles.balance_score <= 1
    assert 0 < principles.whitespace_ratio < 1
    assert principles.scale_ratio > 1
    assert principles.rhythm_unit == 8
    assert principles.proximity_groups
    assert principles.gestalt_grouping == "proximity"
    assert principles.focal_point is LayoutRole.PRODUCT
    assert principles.visual_flow[0] is LayoutRole.PRODUCT


def test_offer_is_removed_cleanly_when_design_spec_has_no_offer() -> None:
    value = deepcopy(_spec_payload())
    value["regions"]["offer_region"] = None
    value["typography_roles"] = [
        role for role in value["typography_roles"] if role["role"] != "offer"
    ]
    spec = DesignSpec(**value, contract_fingerprint="a" * 64)

    plan = LayoutEngine().build(spec)

    assert LayoutRole.OFFER not in {item.role for item in plan.placements}
    assert len(plan.placements) == 4


def test_layout_is_deterministic_and_does_not_mutate_design_spec() -> None:
    spec = _spec()
    before = spec.model_dump(mode="json")

    first = LayoutEngine().build(spec)
    second = LayoutEngine().build(spec)

    assert first == second
    assert spec.model_dump(mode="json") == before


def _placement(role: LayoutRole, *, y: int, height: int) -> LayoutPlacement:
    return LayoutPlacement(
        role=role,
        x=64,
        y=y,
        width=300,
        height=height,
        alignment=Alignment.LEFT,
        priority=50,
        z_index=5,
        constraints=[
            {"kind": "safe_area", "axis": "both", "value": "contain"},
            {"kind": "grid", "axis": "both", "value": "snap"},
        ],
    )
