import colorsys
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.design_spec import DesignSpec

from .typography import TextRole, TypographyPlan

COLOR_PLAN_SCHEMA_VERSION = "1.0"


class ColorHardFailure(StrEnum):
    UNAPPROVED_BRAND_COLOR = "unapproved_brand_color"
    INVALID_NEUTRAL = "invalid_neutral"
    TEXT_CONTRAST = "text_contrast"
    CTA_CONTRAST = "cta_contrast"
    PRODUCT_SEPARATION = "product_background_separation"
    RANDOM_GRADIENT = "random_gradient"
    CONTRACT_DRIFT = "contract_drift"


class ColorEngineError(ValueError):
    def __init__(self, failure: ColorHardFailure, detail: str) -> None:
        self.failure = failure
        self.detail = detail
        super().__init__(f"{failure.value}: {detail}")


class GradientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    colors: list[str] = Field(min_length=2, max_length=4)
    angle_degrees: int = Field(ge=0, lt=360)
    approved: bool = False
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("colors")
    @classmethod
    def colors_are_hex(cls, values: list[str]) -> list[str]:
        return [_hex(value) for value in values]


class ColorEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design_spec: DesignSpec
    typography_plan: TypographyPlan
    approved_brand_palette: list[str] = Field(min_length=1, max_length=50)
    product_colors: list[str] = Field(min_length=1, max_length=20)
    objective: str = Field(min_length=1, max_length=500)
    mood: str = Field(min_length=1, max_length=500)
    gradient: GradientRequest | None = None

    @field_validator("approved_brand_palette", "product_colors")
    @classmethod
    def normalize_colors(cls, values: list[str]) -> list[str]:
        normalized = [_hex(value) for value in values]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def inputs_share_contract(self) -> "ColorEngineInput":
        if (
            self.design_spec.contract_fingerprint
            != self.typography_plan.contract_fingerprint
        ):
            raise ValueError("color inputs disagree on the semantic contract")
        return self


class ContrastCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    foreground_role: str
    background_role: str
    foreground: str
    background: str
    ratio: float = Field(ge=1, le=21)
    minimum_ratio: float = Field(ge=1, le=21)
    passed: bool


class ProductSeparation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background: str
    minimum_ratio: float = Field(ge=1, le=21)
    threshold: float = Field(ge=1, le=21)
    passed: bool
    treatment: str


class ResolvedGradient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    colors: list[str]
    angle_degrees: int
    reason: str


class ColorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = COLOR_PLAN_SCHEMA_VERSION
    brand_palette: list[str]
    dominant: str
    secondary: str
    accent: str
    background: str
    text_color: str
    cta_background: str
    cta_text: str
    contrast_checks: list[ContrastCheck] = Field(min_length=2)
    product_separation: ProductSeparation
    harmony_score: float = Field(ge=0, le=1)
    objective: str
    mood: str
    gradient: ResolvedGradient | None = None
    hard_failures: list[ColorHardFailure] = Field(default_factory=list, max_length=0)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class ColorContrastEngine:
    def build(self, payload: ColorEngineInput) -> ColorPlan:
        tokens = {token.role: _hex(token.value) for token in payload.design_spec.color_system}
        approved = set(payload.approved_brand_palette)
        for token in payload.design_spec.color_system:
            value = _hex(token.value)
            if token.source == "brand" and value not in approved:
                raise ColorEngineError(
                    ColorHardFailure.UNAPPROVED_BRAND_COLOR,
                    f"{token.role} uses unapproved color {value}",
                )
            if token.source == "neutral" and _chroma(value) > 0.15:
                raise ColorEngineError(
                    ColorHardFailure.INVALID_NEUTRAL,
                    f"{value} is too chromatic to be treated as neutral",
                )

        background = _required(tokens, "background")
        text = _required(tokens, "text")
        accent = _required(tokens, "accent")
        cta_background = tokens.get("cta_background", accent)
        cta_text = tokens.get("cta_text", text)
        checks = [
            _check("text", "background", text, background, 4.5),
            _check("cta_text", "cta_background", cta_text, cta_background, 4.5),
        ]
        if not checks[0].passed:
            raise ColorEngineError(
                ColorHardFailure.TEXT_CONTRAST,
                f"text contrast is {checks[0].ratio}:1; minimum is 4.5:1",
            )
        if not checks[1].passed:
            raise ColorEngineError(
                ColorHardFailure.CTA_CONTRAST,
                f"CTA contrast is {checks[1].ratio}:1; minimum is 4.5:1",
            )
        _validate_typography_contrast(payload.typography_plan, checks)

        separation_ratio = min(
            _contrast(color, background) for color in payload.product_colors
        )
        if separation_ratio < 1.25:
            raise ColorEngineError(
                ColorHardFailure.PRODUCT_SEPARATION,
                f"product/background separation is only {separation_ratio:.2f}:1",
            )
        separation = ProductSeparation(
            background=background,
            minimum_ratio=round(separation_ratio, 2),
            threshold=1.5,
            passed=separation_ratio >= 1.5,
            treatment=(
                "none"
                if separation_ratio >= 1.5
                else "add an approved neutral separation plate or subtle edge treatment"
            ),
        )
        gradient = _resolve_gradient(payload, approved, tokens)
        return ColorPlan(
            brand_palette=payload.approved_brand_palette,
            dominant=background,
            secondary=text,
            accent=accent,
            background=background,
            text_color=text,
            cta_background=cta_background,
            cta_text=cta_text,
            contrast_checks=checks,
            product_separation=separation,
            harmony_score=_harmony_score(tokens),
            objective=payload.objective,
            mood=payload.mood,
            gradient=gradient,
            contract_fingerprint=payload.design_spec.contract_fingerprint,
        )


def _resolve_gradient(
    payload: ColorEngineInput,
    approved: set[str],
    tokens: dict[str, str],
) -> ResolvedGradient | None:
    request = payload.gradient
    if request is None:
        return None
    allowed = approved | set(tokens.values())
    context = f"{payload.objective} {payload.mood}".casefold()
    reason = request.reason.casefold()
    context_words = {word for word in context.split() if len(word) >= 4}
    grounded = any(word in reason for word in context_words)
    if not request.approved or not grounded or not set(request.colors).issubset(allowed):
        raise ColorEngineError(
            ColorHardFailure.RANDOM_GRADIENT,
            "gradient is not approved, palette-bound and grounded in objective or mood",
        )
    return ResolvedGradient(
        colors=request.colors,
        angle_degrees=request.angle_degrees,
        reason=request.reason,
    )


def _validate_typography_contrast(
    typography: TypographyPlan,
    checks: list[ContrastCheck],
) -> None:
    expected = {
        TextRole.CTA: checks[1].ratio,
    }
    for block in typography.blocks:
        target = expected.get(block.role, checks[0].ratio)
        if abs(block.contrast_ratio - target) > 0.02:
            raise ColorEngineError(
                ColorHardFailure.CONTRACT_DRIFT,
                f"{block.role.value} contrast disagrees with the color system",
            )


def _check(
    foreground_role: str,
    background_role: str,
    foreground: str,
    background: str,
    minimum: float,
) -> ContrastCheck:
    ratio = round(_contrast(foreground, background), 2)
    return ContrastCheck(
        foreground_role=foreground_role,
        background_role=background_role,
        foreground=foreground,
        background=background,
        ratio=ratio,
        minimum_ratio=minimum,
        passed=ratio >= minimum,
    )


def _harmony_score(tokens: dict[str, str]) -> float:
    unique = set(tokens.values())
    saturated = sum(_saturation(color) > 0.35 for color in unique)
    penalty = max(0, saturated - 1) * 0.12 + max(0, len(unique) - 5) * 0.08
    return round(max(0.0, 1.0 - penalty), 2)


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
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


def _saturation(value: str) -> float:
    red, green, blue = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    return colorsys.rgb_to_hls(red, green, blue)[2]


def _chroma(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    return max(channels) - min(channels)


def _required(tokens: dict[str, str], role: str) -> str:
    try:
        return tokens[role]
    except KeyError as exc:
        raise ValueError(f"color system is missing {role}") from exc


def _hex(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ValueError("colors must use six-digit hex notation")
    try:
        int(normalized[1:], 16)
    except ValueError as exc:
        raise ValueError("colors must use six-digit hex notation") from exc
    return normalized


__all__ = [
    "COLOR_PLAN_SCHEMA_VERSION",
    "ColorContrastEngine",
    "ColorEngineError",
    "ColorEngineInput",
    "ColorHardFailure",
    "ColorPlan",
    "ContrastCheck",
    "GradientRequest",
    "ProductSeparation",
    "ResolvedGradient",
]
