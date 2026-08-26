from collections.abc import Iterable

from app.modules.posts.agents.design_spec import Bounds, DesignSpec, Grid

from .schemas import (
    Alignment,
    ConstraintKind,
    GridGeometry,
    LayoutConstraint,
    LayoutPlacement,
    LayoutPlan,
    LayoutPrinciples,
    LayoutRole,
    SpacingRelation,
)


class SafeAreaEngine:
    def bounds(self, spec: DesignSpec) -> Bounds:
        safe = spec.safe_area
        return Bounds(
            x=safe.left,
            y=safe.top,
            width=spec.canvas.width - safe.left - safe.right,
            height=spec.canvas.height - safe.top - safe.bottom,
        )

    def constrain(self, bounds: Bounds, spec: DesignSpec, *, allow_bleed: bool) -> Bounds:
        container = Bounds(
            x=0,
            y=0,
            width=spec.canvas.width,
            height=spec.canvas.height,
        ) if allow_bleed else self.bounds(spec)
        return _clamp(bounds, container)


class GridEngine:
    def geometry(self, bounds: Bounds, grid: Grid) -> GridGeometry:
        return GridGeometry(
            x_lines=_track_lines(bounds.x, bounds.width, grid.columns, grid.gutter),
            y_lines=_track_lines(bounds.y, bounds.height, grid.rows, grid.gutter),
            gutter=grid.gutter,
            baseline=grid.baseline,
        )

    def snap(self, bounds: Bounds, geometry: GridGeometry, *, container: Bounds) -> Bounds:
        left = _nearest(geometry.x_lines, bounds.x)
        right = _nearest(geometry.x_lines, bounds.x + bounds.width)
        top = _nearest(geometry.y_lines, bounds.y)
        bottom = _nearest(geometry.y_lines, bounds.y + bounds.height)
        if right <= left:
            right = min(container.x + container.width, left + geometry.baseline)
        if bottom <= top:
            bottom = min(container.y + container.height, top + geometry.baseline)
        return _clamp(
            Bounds(x=left, y=top, width=right - left, height=bottom - top),
            container,
        )


class SpacingEngine:
    def relations(
        self,
        placements: list[LayoutPlacement],
        *,
        baseline: int,
    ) -> list[SpacingRelation]:
        flow = [
            item
            for role in (LayoutRole.HEADLINE, LayoutRole.OFFER, LayoutRole.CTA, LayoutRole.LOGO)
            for item in placements
            if item.role is role
        ]
        relations: list[SpacingRelation] = []
        for before, after in zip(flow, flow[1:], strict=False):
            gap = after.y - before.bottom
            if gap < baseline:
                raise ValueError(
                    f"spacing between {before.role} and {after.role} is below baseline"
                )
            relations.append(
                SpacingRelation(
                    before=before.role,
                    after=after.role,
                    axis="y",
                    gap=gap,
                    minimum_gap=baseline,
                )
            )
        return relations


class VisualHierarchyPlanner:
    _ORDER = (
        LayoutRole.PRODUCT,
        LayoutRole.HEADLINE,
        LayoutRole.OFFER,
        LayoutRole.CTA,
        LayoutRole.LOGO,
    )

    def priorities(self, roles: Iterable[LayoutRole]) -> dict[LayoutRole, int]:
        present = set(roles)
        return {
            role: 100 - index * 15
            for index, role in enumerate(self._ORDER)
            if role in present
        }

    def visual_flow(self, roles: Iterable[LayoutRole]) -> list[LayoutRole]:
        present = set(roles)
        return [role for role in self._ORDER if role in present]


class LayoutEngine:
    def __init__(self) -> None:
        self.safe_area = SafeAreaEngine()
        self.grid = GridEngine()
        self.spacing = SpacingEngine()
        self.hierarchy = VisualHierarchyPlanner()

    def build(self, spec: DesignSpec) -> LayoutPlan:
        safe_bounds = self.safe_area.bounds(spec)
        safe_grid = self.grid.geometry(safe_bounds, spec.grid)
        canvas_bounds = Bounds(x=0, y=0, width=spec.canvas.width, height=spec.canvas.height)
        canvas_grid = self.grid.geometry(canvas_bounds, spec.grid)
        raw = _region_map(spec)
        priorities = self.hierarchy.priorities(raw)
        alignments = _alignment_map(spec)
        placements: list[LayoutPlacement] = []
        for role, bounds in raw.items():
            allow_bleed = role is LayoutRole.PRODUCT
            container = canvas_bounds if allow_bleed else safe_bounds
            constrained = self.safe_area.constrain(bounds, spec, allow_bleed=allow_bleed)
            snapped = self.grid.snap(
                constrained,
                canvas_grid if allow_bleed else safe_grid,
                container=container,
            )
            constraint_kind = ConstraintKind.CANVAS if allow_bleed else ConstraintKind.SAFE_AREA
            placements.append(
                LayoutPlacement(
                    role=role,
                    x=snapped.x,
                    y=snapped.y,
                    width=snapped.width,
                    height=snapped.height,
                    alignment=alignments.get(role, Alignment.LEFT),
                    priority=priorities[role],
                    z_index=priorities[role] // 10,
                    constraints=[
                        LayoutConstraint(kind=constraint_kind, axis="both", value="contain"),
                        LayoutConstraint(kind=ConstraintKind.GRID, axis="both", value="snap"),
                        LayoutConstraint(
                            kind=ConstraintKind.MIN_SPACING,
                            axis="y",
                            minimum=spec.grid.baseline,
                        ),
                    ],
                )
            )
        placements.sort(key=lambda item: -item.priority)
        spacing = self.spacing.relations(placements, baseline=spec.grid.baseline)
        flow = self.hierarchy.visual_flow(item.role for item in placements)
        return LayoutPlan(
            canvas=spec.canvas,
            safe_bounds=safe_bounds,
            grid=safe_grid,
            placements=placements,
            spacing=spacing,
            principles=_measure_principles(spec, placements, flow),
            source_design_spec_version=spec.schema_version,
            contract_fingerprint=spec.contract_fingerprint,
        )


def _region_map(spec: DesignSpec) -> dict[LayoutRole, Bounds]:
    regions = spec.regions
    result = {
        LayoutRole.PRODUCT: regions.product_bounds,
        LayoutRole.HEADLINE: regions.headline_region,
        LayoutRole.CTA: regions.cta_region,
        LayoutRole.LOGO: regions.logo_region,
    }
    if regions.offer_region is not None:
        result[LayoutRole.OFFER] = regions.offer_region
    return result


def _alignment_map(spec: DesignSpec) -> dict[LayoutRole, Alignment]:
    mapped: dict[LayoutRole, Alignment] = {}
    for typography in spec.typography_roles:
        try:
            role = LayoutRole(typography.role)
        except ValueError:
            continue
        mapped[role] = Alignment(typography.align)
    return mapped


def _measure_principles(
    spec: DesignSpec,
    placements: list[LayoutPlacement],
    flow: list[LayoutRole],
) -> LayoutPrinciples:
    canvas_area = spec.canvas.width * spec.canvas.height
    occupied = min(canvas_area, sum(item.width * item.height for item in placements))
    weighted_center = sum(
        (item.x + item.width / 2) * item.width * item.height for item in placements
    ) / max(1, occupied)
    center_delta = abs(weighted_center - spec.canvas.width / 2) / (spec.canvas.width / 2)
    product = next(item for item in placements if item.role is LayoutRole.PRODUCT)
    headline = next(item for item in placements if item.role is LayoutRole.HEADLINE)
    safe = Bounds(
        x=spec.safe_area.left,
        y=spec.safe_area.top,
        width=spec.canvas.width - spec.safe_area.left - spec.safe_area.right,
        height=spec.canvas.height - spec.safe_area.top - spec.safe_area.bottom,
    )
    safe_lines = set(_track_lines(safe.x, safe.width, spec.grid.columns, spec.grid.gutter))
    canvas_lines = set(
        _track_lines(0, spec.canvas.width, spec.grid.columns, spec.grid.gutter)
    )
    snapped_edges = sum(
        int(
            item.x in (canvas_lines if item.role is LayoutRole.PRODUCT else safe_lines)
            and item.right in (canvas_lines if item.role is LayoutRole.PRODUCT else safe_lines)
        )
        for item in placements
    )
    return LayoutPrinciples(
        alignment_score=round(snapped_edges / len(placements), 4),
        balance_score=round(max(0.0, 1 - center_delta), 4),
        whitespace_ratio=round(max(0.0, 1 - occupied / canvas_area), 4),
        scale_ratio=round((product.width * product.height) / (headline.width * headline.height), 4),
        rhythm_unit=spec.grid.baseline,
        proximity_groups=[[role for role in flow if role is not LayoutRole.PRODUCT]],
        gestalt_grouping="proximity",
        focal_point=LayoutRole.PRODUCT,
        visual_flow=flow,
    )
def _track_lines(start: int, size: int, count: int, gutter: int) -> list[int]:
    track = (size - gutter * (count - 1)) / count
    if track <= 0:
        raise ValueError("grid gutters consume the available area")
    lines = [start]
    for index in range(1, count):
        lines.append(round(start + index * (track + gutter)))
    lines.append(start + size)
    return list(dict.fromkeys(lines))


def _nearest(lines: list[int], value: int) -> int:
    return min(lines, key=lambda line: (abs(line - value), line))


def _clamp(bounds: Bounds, container: Bounds) -> Bounds:
    left = max(container.x, min(bounds.x, container.x + container.width - 1))
    top = max(container.y, min(bounds.y, container.y + container.height - 1))
    right = min(container.x + container.width, max(left + 1, bounds.x + bounds.width))
    bottom = min(container.y + container.height, max(top + 1, bounds.y + bounds.height))
    return Bounds(x=left, y=top, width=right - left, height=bottom - top)


__all__ = [
    "GridEngine",
    "LayoutEngine",
    "SafeAreaEngine",
    "SpacingEngine",
    "VisualHierarchyPlanner",
]
