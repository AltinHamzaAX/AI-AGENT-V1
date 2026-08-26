import hashlib
import io
import warnings

from PIL import Image, ImageDraw, UnidentifiedImageError

from app.modules.posts.agents.design_spec import Bounds, DesignSpec, GraphicElement
from app.modules.posts.tools.design import Alignment, TextBlock, TextRole, TypographyPlan

from .fonts import FontFace, FontLibrary
from .schemas import (
    ComponentKind,
    ComponentMetadata,
    CompositionError,
    CompositionFailure,
    RenderedAsset,
    SourceVisual,
)


def open_source(source: SourceVisual) -> Image.Image:
    source.verified_checksum()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source.image_bytes)) as image:
                actual_mime = image.get_format_mimetype()
                image.load()
                result = image.convert("RGBA")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise CompositionError(
            CompositionFailure.INVALID_IMAGE,
            f"asset {source.asset_id} is unreadable or unsafe",
        ) from exc
    if actual_mime != source.mime_type:
        raise CompositionError(
            CompositionFailure.MIME_MISMATCH,
            f"asset {source.asset_id} decoded as {actual_mime}, not {source.mime_type}",
        )
    return result


class AssetCompositor:
    def render(
        self,
        canvas: Image.Image,
        source: SourceVisual,
        bounds: Bounds,
        *,
        z_index: int,
        component_id: str,
    ) -> ComponentMetadata:
        image = open_source(source)
        fitted, actual = _contain(image, bounds)
        canvas.alpha_composite(fitted, (actual.x, actual.y))
        return _component(
            component_id=component_id,
            kind=ComponentKind.PRODUCT,
            bounds=actual,
            z_index=z_index,
            image=fitted,
            source=source,
            identity_preserved=True,
            detail="Original source pixels composited with aspect ratio preserved.",
        )


class LogoRenderer:
    def render(
        self, canvas: Image.Image, source: SourceVisual, bounds: Bounds, *, z_index: int
    ) -> ComponentMetadata:
        image = open_source(source)
        fitted, actual = _contain(image, bounds)
        canvas.alpha_composite(fitted, (actual.x, actual.y))
        return _component(
            component_id="logo",
            kind=ComponentKind.LOGO,
            bounds=actual,
            z_index=z_index,
            image=fitted,
            source=source,
            identity_preserved=True,
            detail="Original logo composited without redrawing or substitution.",
        )


class GraphicElementRenderer:
    def render(
        self,
        canvas: Image.Image,
        element: GraphicElement,
        spec: DesignSpec,
        *,
        index: int,
        z_index: int,
    ) -> ComponentMetadata:
        colors = {token.role: token.value for token in spec.color_system}
        color = _rgba(colors[element.color_role], element.opacity)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        box = _box(element.region)
        width = max(1, round(spec.grid.baseline / 2))
        if element.kind in {"shape", "motif", "texture"}:
            draw.rectangle(box, fill=color)
        elif element.kind == "line":
            y = element.region.y + element.region.height // 2
            draw.line(
                (element.region.x, y, element.region.x + element.region.width, y),
                fill=color,
                width=width,
            )
        else:
            draw.rectangle(box, outline=color, width=width)
        canvas.alpha_composite(layer)
        return _component(
            component_id=f"graphic-{index}",
            kind=ComponentKind.GRAPHIC_ELEMENT,
            bounds=element.region,
            z_index=z_index,
            image=layer.crop(box),
            detail=f"Deterministic {element.kind} using color role {element.color_role}.",
        )


class TypographyRenderer:
    def __init__(self, fonts: FontLibrary | None = None) -> None:
        self._fonts = fonts or FontLibrary()

    def render(
        self, canvas: Image.Image, plan: TypographyPlan, spec: DesignSpec, *, z_index: int
    ) -> list[ComponentMetadata]:
        colors = {token.role: token.value for token in spec.color_system}
        metadata: list[ComponentMetadata] = []
        for block in plan.blocks:
            face = self._fonts.load(block.font_family, block.weight, block.font_size_px)
            _assert_fits(block, face)
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            if block.role is TextRole.CTA:
                draw.rounded_rectangle(
                    _box(block.bounds),
                    radius=max(4, spec.grid.baseline),
                    fill=colors["cta_background"],
                )
                fill = colors["cta_text"]
            else:
                fill = colors["text"]
            _draw_block(draw, block, face, fill)
            canvas.alpha_composite(layer)
            kind = (
                ComponentKind.CTA
                if block.role is TextRole.CTA
                else ComponentKind.OFFER
                if block.role is TextRole.OFFER
                else ComponentKind.TYPOGRAPHY
            )
            metadata.append(
                _component(
                    component_id=f"text-{block.role.value}",
                    kind=kind,
                    bounds=block.bounds,
                    z_index=z_index,
                    image=layer.crop(_box(block.bounds)),
                    text=block.text,
                    detail=(
                        f"Exact {block.role.value} rendered at {block.font_size_px}px "
                        f"with {block.alignment.value} alignment in {face.describe()}."
                    ),
                )
            )
        return metadata


class ExportRenderer:
    def export(
        self, canvas: Image.Image, *, final_scale: int
    ) -> tuple[RenderedAsset, RenderedAsset, RenderedAsset]:
        if max(canvas.width, canvas.height) * final_scale > 8192:
            raise CompositionError(
                CompositionFailure.EXPORT_TOO_LARGE,
                "final export exceeds the 8192px dimension limit",
            )
        working = _png(canvas)
        preview_ratio = min(1.0, 720 / max(canvas.size))
        preview_image = canvas.resize(
            (
                max(1, round(canvas.width * preview_ratio)),
                max(1, round(canvas.height * preview_ratio)),
            ),
            Image.Resampling.LANCZOS,
        )
        preview = _png(preview_image)
        final_image = (
            canvas
            if final_scale == 1
            else canvas.resize(
                (canvas.width * final_scale, canvas.height * final_scale),
                Image.Resampling.LANCZOS,
            )
        )
        final = _png(final_image)
        return working, preview, final


def _contain(image: Image.Image, bounds: Bounds) -> tuple[Image.Image, Bounds]:
    ratio = min(bounds.width / image.width, bounds.height / image.height)
    width = max(1, round(image.width * ratio))
    height = max(1, round(image.height * ratio))
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    actual = Bounds(
        x=bounds.x + (bounds.width - width) // 2,
        y=bounds.y + (bounds.height - height) // 2,
        width=width,
        height=height,
    )
    return resized, actual


def _line_width(face: FontFace, text: str, letter_spacing: float) -> float:
    if not letter_spacing:
        return face.font.getlength(text)
    return sum(face.font.getlength(character) for character in text) + max(
        0, len(text) - 1
    ) * letter_spacing


def _assert_fits(block: TextBlock, face: FontFace) -> None:
    """Guard the gap between the planner's glyph estimate and real metrics."""
    width = max(_line_width(face, line, block.letter_spacing_px) for line in block.lines)
    if width > block.bounds.width:
        raise CompositionError(
            CompositionFailure.TEXT_OVERFLOW,
            f"{block.role.value} renders {round(width)}px wide in {face.family} "
            f"but its region is {block.bounds.width}px",
        )
    height = block.line_height_px * len(block.lines)
    if height > block.bounds.height:
        raise CompositionError(
            CompositionFailure.TEXT_OVERFLOW,
            f"{block.role.value} renders {height}px tall "
            f"but its region is {block.bounds.height}px",
        )


def _draw_block(
    draw: ImageDraw.ImageDraw,
    block: TextBlock,
    face: FontFace,
    fill: str,
) -> None:
    stroke_width = 1 if face.synthetic_bold else 0
    y = block.bounds.y
    for line in block.lines:
        width = _line_width(face, line, block.letter_spacing_px)
        if block.alignment is Alignment.CENTER:
            x = block.bounds.x + (block.bounds.width - width) / 2
        elif block.alignment is Alignment.RIGHT:
            x = block.bounds.x + block.bounds.width - width
        else:
            x = block.bounds.x
        _draw_line(
            draw,
            line,
            x=x,
            y=y,
            face=face,
            fill=fill,
            letter_spacing=block.letter_spacing_px,
            stroke_width=stroke_width,
        )
        y += block.line_height_px


def _draw_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: float,
    y: int,
    face: FontFace,
    fill: str,
    letter_spacing: float,
    stroke_width: int,
) -> None:
    if not letter_spacing:
        draw.text(
            (round(x), y),
            text,
            font=face.font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=fill,
        )
        return
    # Pillow lays out a whole string with kerning only, so tracked text has to be
    # advanced glyph by glyph to honour the spec's letter spacing.
    cursor = x
    for character in text:
        draw.text(
            (round(cursor), y),
            character,
            font=face.font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=fill,
        )
        cursor += face.font.getlength(character) + letter_spacing


def _component(
    *,
    component_id: str,
    kind: ComponentKind,
    bounds: Bounds,
    z_index: int,
    image: Image.Image,
    detail: str,
    source: SourceVisual | None = None,
    identity_preserved: bool | None = None,
    text: str | None = None,
) -> ComponentMetadata:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return ComponentMetadata(
        component_id=component_id,
        kind=kind,
        bounds=bounds,
        z_index=z_index,
        source_asset_id=source.asset_id if source else None,
        source_checksum=source.verified_checksum() if source else None,
        rendered_checksum=hashlib.sha256(buffer.getvalue()).hexdigest(),
        identity_preserved=identity_preserved,
        text=text,
        detail=detail,
    )


def _rgba(value: str, opacity: float) -> tuple[int, int, int, int]:
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
        round(opacity * 255),
    )


def _box(bounds: Bounds) -> tuple[int, int, int, int]:
    return (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)


def _png(image: Image.Image) -> RenderedAsset:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    data = buffer.getvalue()
    return RenderedAsset(
        image_bytes=data,
        mime_type="image/png",
        width=image.width,
        height=image.height,
        checksum=hashlib.sha256(data).hexdigest(),
    )


__all__ = [
    "AssetCompositor",
    "ExportRenderer",
    "GraphicElementRenderer",
    "LogoRenderer",
    "TypographyRenderer",
    "open_source",
]
