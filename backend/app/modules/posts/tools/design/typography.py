import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import Bounds, DesignSpec, TypographyRole

from .schemas import Alignment, LayoutPlan, LayoutRole

TYPOGRAPHY_PLAN_SCHEMA_VERSION = "1.0"


class TextRole(StrEnum):
    HEADLINE = "headline"
    SUBHEADLINE = "subheadline"
    OFFER = "offer"
    SUPPORTING = "supporting"
    CTA = "cta"
    LEGAL = "legal"


class FitStatus(StrEnum):
    FIT = "fit"
    ADJUSTED = "adjusted"


class TypographyHardFailure(StrEnum):
    CLIPPING = "clipping"
    OVERLAP = "overlap"
    UNREADABLE = "unreadable_text"
    OUTSIDE_SAFE_AREA = "outside_safe_area"
    OVERFLOW = "overflow"
    FONT_UNAVAILABLE = "font_unavailable"


class TypographyLayoutError(ValueError):
    def __init__(self, failure: TypographyHardFailure, detail: str) -> None:
        self.failure = failure
        self.detail = detail
        super().__init__(f"{failure.value}: {detail}")


class TypographyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design_spec: DesignSpec
    layout_plan: LayoutPlan
    copy_draft: CopyDraft
    legal_text: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def inputs_share_identity(self) -> "TypographyInput":
        fingerprints = {
            self.design_spec.contract_fingerprint,
            self.layout_plan.contract_fingerprint,
            self.copy_draft.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("typography inputs disagree on the semantic contract")
        if self.layout_plan.source_design_spec_version != self.design_spec.schema_version:
            raise ValueError("layout plan targets a different DesignSpec version")
        return self


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: TextRole
    text: str
    lines: list[str] = Field(min_length=1)
    font_family: str
    weight: int
    font_size_px: int
    line_height_px: int
    letter_spacing_px: float
    max_lines: int
    text_width_px: int
    alignment: Alignment
    priority: int = Field(ge=1, le=100)
    bounds: Bounds
    contrast_ratio: float = Field(ge=1, le=21)
    fit_status: FitStatus


class TypographyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = TYPOGRAPHY_PLAN_SCHEMA_VERSION
    blocks: list[TextBlock] = Field(min_length=2, max_length=6)
    overflow: bool = False
    hard_failures: list[TypographyHardFailure] = Field(default_factory=list, max_length=0)
    hierarchy: list[TextRole] = Field(min_length=2)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class TypographyEngine:
    _MIN_SIZE = {
        TextRole.HEADLINE: 24,
        TextRole.SUBHEADLINE: 16,
        TextRole.OFFER: 18,
        TextRole.SUPPORTING: 16,
        TextRole.CTA: 16,
        TextRole.LEGAL: 10,
    }

    def __init__(self, available_families: set[str] | None = None) -> None:
        self._available_families = available_families

    def build(self, payload: TypographyInput) -> TypographyPlan:
        self._validate_safe_area(payload.layout_plan)
        styles = {style.role: style for style in payload.design_spec.typography_roles}
        text_by_role = _text_by_role(payload)
        requested = [
            role for role in _role_order() if role in text_by_role and _style_key(role) in styles
        ]
        hierarchy = [role for role in _role_order() if role in requested]
        placements = {item.role: item for item in payload.layout_plan.placements}
        blocks: list[TextBlock] = []

        headline_roles = [
            role
            for role in (TextRole.HEADLINE, TextRole.SUBHEADLINE, TextRole.SUPPORTING)
            if role in requested
        ]
        if headline_roles:
            container = placements[LayoutRole.HEADLINE]
            blocks.extend(
                self._fit_group(
                    headline_roles,
                    text_by_role,
                    styles,
                    container.bounds if hasattr(container, "bounds") else Bounds(
                        x=container.x,
                        y=container.y,
                        width=container.width,
                        height=container.height,
                    ),
                    priority=container.priority,
                    baseline=payload.design_spec.grid.baseline,
                    colors=payload.design_spec.color_system,
                )
            )
        for role, layout_role in (
            (TextRole.OFFER, LayoutRole.OFFER),
            (TextRole.CTA, LayoutRole.CTA),
            (TextRole.LEGAL, LayoutRole.LEGAL),
        ):
            if role not in requested:
                continue
            container = placements.get(layout_role)
            if container is None:
                raise TypographyLayoutError(
                    TypographyHardFailure.OVERFLOW, f"no region exists for {role.value}"
                )
            bounds = Bounds(
                x=container.x, y=container.y, width=container.width, height=container.height
            )
            blocks.append(
                self._fit_block(
                    role,
                    text_by_role[role],
                    styles[_style_key(role)],
                    bounds,
                    priority=container.priority,
                    colors=payload.design_spec.color_system,
                )
            )
        _assert_no_overlap(blocks)
        return TypographyPlan(
            blocks=blocks,
            hierarchy=hierarchy,
            contract_fingerprint=payload.design_spec.contract_fingerprint,
        )

    def _fit_group(
        self,
        roles: list[TextRole],
        texts: dict[TextRole, str],
        styles: dict[str, TypographyRole],
        container: Bounds,
        *,
        priority: int,
        baseline: int,
        colors: list,
    ) -> list[TextBlock]:
        gaps = baseline * (len(roles) - 1)
        available = container.height - gaps
        if available <= 0:
            raise TypographyLayoutError(
                TypographyHardFailure.OVERFLOW, "headline group has no usable height"
            )
        weights = [2 if role is TextRole.HEADLINE else 1 for role in roles]
        weight_total = sum(weights)
        y = container.y
        blocks: list[TextBlock] = []
        used = 0
        for index, (role, weight) in enumerate(zip(roles, weights, strict=True)):
            height = (
                available - used
                if index == len(roles) - 1
                else available * weight // weight_total
            )
            bounds = Bounds(x=container.x, y=y, width=container.width, height=max(1, height))
            block = self._fit_block(
                role,
                texts[role],
                styles[_style_key(role)],
                bounds,
                priority=max(1, priority - index * 5),
                colors=colors,
            )
            blocks.append(block)
            consumed = block.bounds.height
            used += consumed
            y += consumed + baseline
        return blocks

    def _fit_block(
        self,
        role: TextRole,
        text: str,
        style: TypographyRole,
        container: Bounds,
        *,
        priority: int,
        colors: list,
    ) -> TextBlock:
        if (
            self._available_families is not None
            and style.family_token not in self._available_families
        ):
            raise TypographyLayoutError(
                TypographyHardFailure.FONT_UNAVAILABLE, style.family_token
            )
        original_size = style.size_px
        for size in range(original_size, self._MIN_SIZE[role] - 1, -1):
            lines = _wrap(text, container.width, size, style.letter_spacing_px)
            line_height = max(1, round(size * style.line_height))
            height = line_height * len(lines)
            if len(lines) <= style.max_lines and height <= container.height:
                contrast = _contrast_for(role, colors)
                threshold = 3.0 if size >= 24 or (size >= 19 and style.weight >= 700) else 4.5
                if contrast < threshold:
                    raise TypographyLayoutError(
                        TypographyHardFailure.UNREADABLE,
                        f"{role.value} contrast {contrast:.2f}:1 is below {threshold}:1",
                    )
                return TextBlock(
                    role=role,
                    text=text,
                    lines=lines,
                    font_family=style.family_token,
                    weight=style.weight,
                    font_size_px=size,
                    line_height_px=line_height,
                    letter_spacing_px=style.letter_spacing_px,
                    max_lines=style.max_lines,
                    text_width_px=max(
                        round(_measure(line, size, style.letter_spacing_px)) for line in lines
                    ),
                    alignment=Alignment(style.align),
                    priority=priority,
                    bounds=Bounds(
                        x=container.x,
                        y=container.y,
                        width=container.width,
                        height=height,
                    ),
                    contrast_ratio=round(contrast, 2),
                    fit_status=FitStatus.FIT if size == original_size else FitStatus.ADJUSTED,
                )
        raise TypographyLayoutError(
            TypographyHardFailure.OVERFLOW,
            f"{role.value} cannot fit within max lines and text region",
        )

    def _validate_safe_area(self, plan: LayoutPlan) -> None:
        safe = plan.safe_bounds
        for placement in plan.placements:
            if placement.role is LayoutRole.PRODUCT:
                continue
            if (
                placement.x < safe.x
                or placement.y < safe.y
                or placement.right > safe.x + safe.width
                or placement.bottom > safe.y + safe.height
            ):
                raise TypographyLayoutError(
                    TypographyHardFailure.OUTSIDE_SAFE_AREA,
                    f"{placement.role.value} placement is outside safe area",
                )


def _text_by_role(payload: TypographyInput) -> dict[TextRole, str]:
    copy = payload.copy_draft
    values = {
        TextRole.HEADLINE: copy.headline,
        TextRole.SUBHEADLINE: copy.subheadline,
        TextRole.OFFER: copy.offer_copy,
        TextRole.SUPPORTING: copy.supporting_copy,
        TextRole.CTA: copy.cta,
        TextRole.LEGAL: payload.legal_text,
    }
    return {role: value for role, value in values.items() if value}


def _role_order() -> tuple[TextRole, ...]:
    return (
        TextRole.HEADLINE,
        TextRole.SUBHEADLINE,
        TextRole.OFFER,
        TextRole.SUPPORTING,
        TextRole.CTA,
        TextRole.LEGAL,
    )


def _style_key(role: TextRole) -> str:
    return "supporting_copy" if role is TextRole.SUPPORTING else role.value


def _wrap(text: str, width: int, size: int, letter_spacing: float) -> list[str]:
    words = re.findall(r"\S+", text)
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _measure(candidate, size, letter_spacing) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if _measure(word, size, letter_spacing) <= width:
            current = word
            continue
        chunks = _split_word(word, width, size, letter_spacing)
        lines.extend(chunks[:-1])
        current = chunks[-1]
    if current:
        lines.append(current)
    return lines or [""]


def _split_word(word: str, width: int, size: int, letter_spacing: float) -> list[str]:
    chunks: list[str] = []
    current = ""
    for character in word:
        candidate = current + character
        if current and _measure(candidate, size, letter_spacing) > width:
            chunks.append(current)
            current = character
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _measure(text: str, size: int, letter_spacing: float) -> float:
    units = sum(_glyph_units(character) for character in text)
    return units * size + max(0, len(text) - 1) * letter_spacing


def _glyph_units(character: str) -> float:
    if character.isspace():
        return 0.33
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 1.0
    if character in "ilI.,'!|:;":
        return 0.28
    if character in "mwMW@#%&":
        return 0.9
    if character.isupper():
        return 0.66
    return 0.54


def _contrast_for(role: TextRole, colors: list) -> float:
    mapped = {token.role: token.value for token in colors}
    foreground = mapped.get("cta_text") if role is TextRole.CTA else mapped.get("text")
    background = mapped.get("cta_background") if role is TextRole.CTA else mapped.get("background")
    if foreground is None or background is None:
        raise TypographyLayoutError(
            TypographyHardFailure.UNREADABLE, f"missing color pair for {role.value}"
        )
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _assert_no_overlap(blocks: list[TextBlock]) -> None:
    for index, first in enumerate(blocks):
        for second in blocks[index + 1 :]:
            if _overlaps(first.bounds, second.bounds):
                raise TypographyLayoutError(
                    TypographyHardFailure.OVERLAP,
                    f"{first.role.value} overlaps {second.role.value}",
                )


def _overlaps(first: Bounds, second: Bounds) -> bool:
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )


__all__ = [
    "TYPOGRAPHY_PLAN_SCHEMA_VERSION",
    "FitStatus",
    "TextBlock",
    "TextRole",
    "TypographyEngine",
    "TypographyHardFailure",
    "TypographyInput",
    "TypographyLayoutError",
    "TypographyPlan",
]
