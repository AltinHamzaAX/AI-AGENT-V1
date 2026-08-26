import hashlib
import io
import json

from PIL import Image

from app.modules.posts.agents.design_spec import Bounds
from app.modules.posts.tools.design import (
    LayoutEngine,
    LayoutRole,
    TypographyEngine,
    TypographyHardFailure,
    TypographyInput,
    TypographyLayoutError,
    TypographyPlan,
)

from .fonts import FontLibrary
from .renderers import (
    AssetCompositor,
    ExportRenderer,
    GraphicElementRenderer,
    LogoRenderer,
    TypographyRenderer,
    open_source,
)
from .schemas import (
    ComponentKind,
    ComponentMetadata,
    ComposerInput,
    CompositionError,
    CompositionFailure,
    CompositionResult,
)


class DeterministicComposer:
    """Assemble approved sources; never invent copy, products, or brand marks."""

    def __init__(self, fonts: FontLibrary | None = None) -> None:
        self.fonts = fonts or FontLibrary()
        self.assets = AssetCompositor()
        self.typography = TypographyRenderer(self.fonts)
        self.logos = LogoRenderer()
        self.graphics = GraphicElementRenderer()
        self.exports = ExportRenderer()
        self.layout = LayoutEngine()
        # The planner may only pick families the renderer can actually load, so
        # a plan that validates is a plan that draws in the face it names.
        self.typography_planner = TypographyEngine(
            available_families=self.fonts.restricted_families()
        )

    def compose(self, payload: ComposerInput) -> CompositionResult:
        payload.enforce_asset_policy()
        spec = payload.design_spec
        canvas = Image.new(
            "RGBA",
            (spec.canvas.width, spec.canvas.height),
            _background_color(spec),
        )
        components: list[ComponentMetadata] = []
        if payload.scene is not None:
            components.append(self._render_scene(canvas, payload.scene))

        for index, element in enumerate(spec.graphic_elements):
            components.append(
                self.graphics.render(
                    canvas,
                    element,
                    spec,
                    index=index,
                    z_index=10 + index,
                )
            )

        layout_plan = self.layout.build(spec)
        placements = {item.role: item for item in layout_plan.placements}
        product_region = placements[LayoutRole.PRODUCT]
        product_bounds = Bounds(
            x=product_region.x,
            y=product_region.y,
            width=product_region.width,
            height=product_region.height,
        )
        for index, (product, bounds) in enumerate(
            zip(payload.products, _split_bounds(product_bounds, len(payload.products)), strict=True)
        ):
            components.append(
                self.assets.render(
                    canvas,
                    product,
                    bounds,
                    z_index=30 + index,
                    component_id=f"product-{index}",
                )
            )

        typography_plan = self._plan_typography(
            TypographyInput(
                design_spec=spec,
                layout_plan=layout_plan,
                copy_draft=payload.copy_draft,
                legal_text=payload.legal_text,
            )
        )
        components.extend(self.typography.render(canvas, typography_plan, spec, z_index=60))

        if payload.logo is not None:
            logo_region = placements[LayoutRole.LOGO]
            components.append(
                self.logos.render(
                    canvas,
                    payload.logo,
                    Bounds(
                        x=logo_region.x,
                        y=logo_region.y,
                        width=logo_region.width,
                        height=logo_region.height,
                    ),
                    z_index=80,
                )
            )

        components.sort(key=lambda item: (item.z_index, item.component_id))
        working, preview, final = self.exports.export(canvas, final_scale=payload.final_scale)
        fingerprint_payload = {
            "contract": spec.contract_fingerprint,
            "working": working.checksum,
            "preview": preview.checksum,
            "final": final.checksum,
            "components": [component.model_dump(mode="json") for component in components],
        }
        render_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True).encode()
        ).hexdigest()
        return CompositionResult(
            working_render=working,
            preview=preview,
            final_asset=final,
            components=components,
            layout_plan=layout_plan,
            typography_plan=typography_plan,
            contract_fingerprint=spec.contract_fingerprint,
            render_fingerprint=render_fingerprint,
        )

    def _plan_typography(self, payload: TypographyInput) -> TypographyPlan:
        try:
            return self.typography_planner.build(payload)
        except TypographyLayoutError as exc:
            if exc.failure is not TypographyHardFailure.FONT_UNAVAILABLE:
                raise
            raise CompositionError(
                CompositionFailure.FONT_UNAVAILABLE,
                f"the design spec asks for unavailable font family '{exc.detail}'",
            ) from exc

    def _render_scene(self, canvas: Image.Image, source) -> ComponentMetadata:
        image = open_source(source)
        ratio = max(canvas.width / image.width, canvas.height / image.height)
        resized = image.resize(
            (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        left = (resized.width - canvas.width) // 2
        top = (resized.height - canvas.height) // 2
        cropped = resized.crop((left, top, left + canvas.width, top + canvas.height))
        canvas.alpha_composite(cropped)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG", optimize=False)
        return ComponentMetadata(
            component_id="scene",
            kind=ComponentKind.SCENE,
            bounds=Bounds(x=0, y=0, width=canvas.width, height=canvas.height),
            z_index=0,
            source_asset_id=source.asset_id,
            source_checksum=source.verified_checksum(),
            rendered_checksum=hashlib.sha256(buffer.getvalue()).hexdigest(),
            identity_preserved=None,
            detail="Scene center-cropped to cover the complete canvas.",
        )


def _background_color(payload) -> tuple[int, int, int, int]:
    colors = {token.role: token.value for token in payload.color_system}
    value = colors["background"]
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 255)


def _split_bounds(bounds: Bounds, count: int) -> list[Bounds]:
    if count == 0:
        return []
    gap = max(0, min(24, bounds.width // 20)) if count > 1 else 0
    usable = bounds.width - gap * (count - 1)
    width = usable // count
    if width <= 0:
        raise ValueError("product region cannot fit all approved product assets")
    result: list[Bounds] = []
    x = bounds.x
    for index in range(count):
        item_width = bounds.x + bounds.width - x if index == count - 1 else width
        result.append(Bounds(x=x, y=bounds.y, width=item_width, height=bounds.height))
        x += item_width + gap
    return result


__all__ = ["DeterministicComposer"]
