from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.design_spec import Bounds, Canvas

LAYOUT_PLAN_SCHEMA_VERSION = "1.0"


class LayoutRole(StrEnum):
    PRODUCT = "product"
    HEADLINE = "headline"
    OFFER = "offer"
    CTA = "cta"
    LOGO = "logo"
    LEGAL = "legal"


class Alignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class ConstraintKind(StrEnum):
    CANVAS = "canvas"
    SAFE_AREA = "safe_area"
    GRID = "grid"
    MIN_SPACING = "min_spacing"
    IDENTITY = "identity"


class LayoutConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ConstraintKind
    axis: Literal["x", "y", "both"]
    minimum: int | None = None
    maximum: int | None = None
    value: int | str | None = None


class GridGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x_lines: list[int] = Field(min_length=2)
    y_lines: list[int] = Field(min_length=2)
    gutter: int = Field(ge=0)
    baseline: int = Field(gt=0)


class LayoutPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: LayoutRole
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    alignment: Alignment
    priority: int = Field(ge=1, le=100)
    z_index: int = Field(ge=0, le=100)
    constraints: list[LayoutConstraint] = Field(min_length=2)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


class SpacingRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before: LayoutRole
    after: LayoutRole
    axis: Literal["x", "y"]
    gap: int = Field(ge=0)
    minimum_gap: int = Field(ge=0)


class LayoutPrinciples(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment_score: float = Field(ge=0, le=1)
    balance_score: float = Field(ge=0, le=1)
    whitespace_ratio: float = Field(ge=0, le=1)
    scale_ratio: float = Field(gt=0)
    rhythm_unit: int = Field(gt=0)
    proximity_groups: list[list[LayoutRole]]
    gestalt_grouping: Literal["proximity", "continuity", "common_region"]
    focal_point: LayoutRole
    visual_flow: list[LayoutRole] = Field(min_length=4)


class LayoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = LAYOUT_PLAN_SCHEMA_VERSION
    canvas: Canvas
    safe_bounds: Bounds
    grid: GridGeometry
    placements: list[LayoutPlacement] = Field(min_length=4, max_length=6)
    spacing: list[SpacingRelation]
    principles: LayoutPrinciples
    source_design_spec_version: str
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def placements_are_unique_and_inside_canvas(self) -> "LayoutPlan":
        roles = [placement.role for placement in self.placements]
        if len(roles) != len(set(roles)):
            raise ValueError("layout placement roles must be unique")
        required = {LayoutRole.PRODUCT, LayoutRole.HEADLINE, LayoutRole.CTA, LayoutRole.LOGO}
        if not required.issubset(roles):
            raise ValueError("layout plan is missing a required placement")
        for placement in self.placements:
            if placement.right > self.canvas.width or placement.bottom > self.canvas.height:
                raise ValueError(f"{placement.role} placement exceeds canvas")
        return self


__all__ = [
    "LAYOUT_PLAN_SCHEMA_VERSION",
    "Alignment",
    "ConstraintKind",
    "GridGeometry",
    "LayoutConstraint",
    "LayoutPlacement",
    "LayoutPlan",
    "LayoutPrinciples",
    "LayoutRole",
    "SpacingRelation",
]
