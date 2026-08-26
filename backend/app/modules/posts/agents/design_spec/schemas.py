from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.domain.semantic_contract import PostSemanticContract

DESIGN_SPEC_SCHEMA_VERSION = "1.0"


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=320, le=8192)
    height: int = Field(ge=320, le=8192)
    unit: Literal["px"] = "px"


class SafeArea(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top: int = Field(ge=0)
    right: int = Field(ge=0)
    bottom: int = Field(ge=0)
    left: int = Field(ge=0)


class Grid(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: int = Field(ge=1, le=24)
    rows: int = Field(ge=1, le=24)
    gutter: int = Field(ge=0, le=512)
    baseline: int = Field(ge=1, le=256)


class Bounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class RegionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_bounds: Bounds
    headline_region: Bounds
    offer_region: Bounds | None = None
    cta_region: Bounds
    logo_region: Bounds


class TypographyRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["headline", "subheadline", "supporting_copy", "offer", "cta"]
    family_token: str = Field(min_length=1, max_length=100)
    weight: int = Field(ge=100, le=900, multiple_of=100)
    size_px: int = Field(ge=8, le=320)
    line_height: float = Field(ge=0.8, le=2.5)
    max_lines: int = Field(ge=1, le=12)
    align: Literal["left", "center", "right"]


class ColorToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["background", "text", "accent", "cta_background", "cta_text"]
    value: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    source: Literal["brand", "neutral"]


class GraphicElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["shape", "line", "frame", "texture", "motif"]
    region: Bounds
    color_role: str = Field(min_length=1, max_length=50)
    opacity: float = Field(ge=0, le=1)
    decorative_only: bool = True


class DesignSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    art_direction: ArtDirection
    copy_draft: CopyDraft
    semantic_contract: dict[str, Any]

    @model_validator(mode="after")
    def inputs_share_contract(self) -> "DesignSpecInput":
        contract = PostSemanticContract.from_dict(self.semantic_contract)
        if len(
            {
                contract.fingerprint,
                self.art_direction.contract_fingerprint,
                self.copy_draft.contract_fingerprint,
            }
        ) != 1:
            raise ValueError("design spec inputs disagree on the semantic contract")
        return self


class DesignSpecBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = DESIGN_SPEC_SCHEMA_VERSION
    canvas: Canvas
    safe_area: SafeArea
    grid: Grid
    regions: RegionSet
    typography_roles: list[TypographyRole] = Field(min_length=2, max_length=5)
    color_system: list[ColorToken] = Field(min_length=3, max_length=5)
    graphic_elements: list[GraphicElement] = Field(default_factory=list, max_length=20)
    photography: str = Field(min_length=1, max_length=1_000)
    lighting: str = Field(min_length=1, max_length=600)
    background: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def geometry_and_roles_are_composer_safe(self) -> "DesignSpecBody":
        if self.safe_area.left + self.safe_area.right >= self.canvas.width:
            raise ValueError("horizontal safe area consumes the canvas")
        if self.safe_area.top + self.safe_area.bottom >= self.canvas.height:
            raise ValueError("vertical safe area consumes the canvas")
        named = {
            "product": self.regions.product_bounds,
            "headline": self.regions.headline_region,
            "cta": self.regions.cta_region,
            "logo": self.regions.logo_region,
        }
        if self.regions.offer_region is not None:
            named["offer"] = self.regions.offer_region
        for name, bounds in named.items():
            if (
                bounds.x + bounds.width > self.canvas.width
                or bounds.y + bounds.height > self.canvas.height
            ):
                raise ValueError(f"{name} region exceeds canvas")
            if name != "product" and not _inside_safe_area(
                bounds, canvas=self.canvas, safe_area=self.safe_area
            ):
                raise ValueError(f"{name} region exceeds safe area")
        for element in self.graphic_elements:
            if (
                element.region.x + element.region.width > self.canvas.width
                or element.region.y + element.region.height > self.canvas.height
            ):
                raise ValueError("graphic element exceeds canvas")
        roles = [item.role for item in self.typography_roles]
        if len(roles) != len(set(roles)) or "headline" not in roles or "cta" not in roles:
            raise ValueError("typography roles must be unique and include headline and CTA")
        colors = [item.role for item in self.color_system]
        if len(colors) != len(set(colors)):
            raise ValueError("color roles must be unique")
        if not {"background", "text", "accent"}.issubset(colors):
            raise ValueError("color system requires background, text and accent")
        return self


class DesignSpec(DesignSpecBody):
    contract_fingerprint: str = Field(min_length=64, max_length=64)


def _inside_safe_area(bounds: Bounds, *, canvas: Canvas, safe_area: SafeArea) -> bool:
    return (
        bounds.x >= safe_area.left
        and bounds.y >= safe_area.top
        and bounds.x + bounds.width <= canvas.width - safe_area.right
        and bounds.y + bounds.height <= canvas.height - safe_area.bottom
    )


__all__ = [
    "DESIGN_SPEC_SCHEMA_VERSION", "Bounds", "Canvas", "ColorToken", "DesignSpec",
    "DesignSpecBody", "DesignSpecInput", "GraphicElement", "Grid", "RegionSet", "SafeArea",
    "TypographyRole",
]
