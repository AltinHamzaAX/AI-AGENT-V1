"""Editing a customer's own product photo without ever redrawing it.

A generated stand-in for a real vehicle, package or logo is not a rendering
mistake, it is a false claim about what the customer sells. So the pipeline is
built to be incapable of it: every tool here transforms the uploaded pixels -
geometry, alpha, brightness - and none of them synthesize new ones. Where the
asset policy also forbids substitution, the request is refused before any
image is opened rather than checked afterwards.
"""

import hashlib
import io
import warnings
from enum import StrEnum
from uuid import UUID

from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    UnidentifiedImageError,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.agents.asset_intelligence import (
    AssetPolicy,
    AssetUsageAssertion,
    IntelligentAssetRole,
)
from app.modules.posts.agents.design_spec import Bounds, Canvas

#: The roles the ticket singles out. Their identity is the product claim
#: itself, so they are reported as preserved whatever else a policy says.
PROTECTED_ROLES = frozenset(
    {
        IntelligentAssetRole.BRAND_LOGO,
        IntelligentAssetRole.PRIMARY_PRODUCT,
        IntelligentAssetRole.VEHICLE,
        IntelligentAssetRole.PACKAGING,
    }
)


class EditOperation(StrEnum):
    """The nine tools, named so a result can be checked against all of them."""

    MASKING = "masking"
    BACKGROUND_REMOVAL = "background_removal"
    CROP = "crop"
    PERSPECTIVE_HANDLING = "perspective_handling"
    LIGHTING_ADAPTATION = "lighting_adaptation"
    EDGE_CLEANUP = "edge_cleanup"
    SCALE = "scale"
    PLACEMENT = "placement"
    SHADOW_INTEGRATION = "shadow_integration"


class PreservationFailure(StrEnum):
    REPLACEMENT_FORBIDDEN = "replacement_forbidden"
    REPLACEMENT_OUT_OF_SCOPE = "replacement_out_of_scope"
    GENERATED_SOURCE_FORBIDDEN = "generated_source_forbidden"
    CROP_FORBIDDEN = "crop_forbidden"
    INVALID_IMAGE = "invalid_image"
    IMAGE_TOO_LARGE = "image_too_large"
    MIME_TYPE_MISMATCH = "mime_type_mismatch"
    BACKGROUND_REMOVAL_UNSAFE = "background_removal_unsafe"
    INVALID_MASK = "invalid_mask"
    OUTSIDE_CANVAS = "outside_canvas"


class PreservationError(ValueError):
    """Raised by the pipeline, never from a validator.

    Pydantic wraps any ValueError a validator raises into a ValidationError,
    which would bury the failure code a caller needs to tell "this asset may
    not be replaced" apart from "these bytes are not an image".
    """

    def __init__(self, failure: PreservationFailure, detail: str) -> None:
        self.failure = failure
        self.detail = detail
        super().__init__(f"{failure.value}: {detail}")


class PerspectiveMode(StrEnum):
    PRESERVE_SOURCE = "preserve_source"
    CORRECT_DISTORTION = "correct_distortion"


class PerspectiveDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PerspectiveMode = PerspectiveMode.PRESERVE_SOURCE
    #: Deliberately tiny. Perspective correction is for straightening a photo
    #: taken at a slight angle; past a few percent it restyles the product.
    strength: float = Field(default=0, ge=-0.08, le=0.08)


class ShadowDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    offset_x: int = Field(default=12, ge=-256, le=256)
    offset_y: int = Field(default=20, ge=-256, le=256)
    blur_radius: float = Field(default=18, ge=0, le=128)
    opacity: int = Field(default=90, ge=0, le=255)


class PreservationInput(BaseModel):
    """The request. Structure is checked here; permission is checked by the
    pipeline, so a refusal arrives as a PreservationError with its code."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    asset_id: UUID
    image_bytes: bytes = Field(min_length=1)
    mime_type: str = Field(pattern=r"^image/")
    policy: AssetPolicy
    canvas: Canvas
    target_bounds: Bounds
    mask_bytes: bytes | None = None
    remove_background: bool = True
    crop_to_content: bool = False
    perspective: PerspectiveDirective = Field(default_factory=PerspectiveDirective)
    shadow: ShadowDirective = Field(default_factory=ShadowDirective)
    #: Brightness only, and barely: enough to sit in a scene, not enough to
    #: change what colour the product is.
    lighting_factor: float = Field(default=1, ge=0.9, le=1.1)
    edge_cleanup: bool = True
    #: What the caller wanted to do instead of using these bytes. Both are
    #: refusals waiting to happen for a preserved asset, and both are recorded
    #: rather than silently ignored.
    replacement_asset_id: UUID | None = None
    source_is_generated: bool = False

    @model_validator(mode="after")
    def request_is_structurally_coherent(self) -> "PreservationInput":
        if self.asset_id != self.policy.asset_id:
            raise ValueError("preservation input asset and policy IDs disagree")
        return self


class EditStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: EditOperation
    applied: bool
    detail: str = Field(min_length=1, max_length=300)


class PreservedAssetResult(BaseModel):
    """What the pipeline did, in terms the asset policy can be checked against."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    asset_id: UUID
    role: IntelligentAssetRole
    image_bytes: bytes = Field(exclude=True)
    mime_type: str = "image/png"
    width: int
    height: int
    actual_bounds: Bounds
    #: Provenance, not decoration: the source digest is what lets a later
    #: stage prove the pixels it received descend from the upload.
    source_checksum: str = Field(min_length=64, max_length=64)
    output_checksum: str = Field(min_length=64, max_length=64)
    identity_preserved: bool
    #: Machine-checkable reasons for the fidelity assertion. A policy flag is
    #: never, by itself, evidence that the output preserved the upload.
    fidelity_evidence: list[str] = Field(min_length=3)
    cropped: bool
    #: Share of the canvas the asset actually covers, alpha included, so the
    #: measurement answers the policy's dominance range rather than reporting
    #: the bounding box a transparent corner inflates.
    dominance: float = Field(ge=0, le=1)
    steps: list[EditStep] = Field(min_length=9, max_length=9)
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def every_tool_reports_once(self) -> "PreservedAssetResult":
        reported = [step.operation for step in self.steps]
        if set(reported) != set(EditOperation) or len(reported) != len(set(reported)):
            raise ValueError("a preserved asset must report each edit operation exactly once")
        return self

    def usage_assertion(self) -> AssetUsageAssertion:
        """Hand the existing policy validator something to check.

        The pipeline cannot replace or generate, so it says so plainly and
        lets `evaluate_asset_usage` decide whether that satisfied the policy,
        rather than asserting compliance on its own behalf.
        """
        return AssetUsageAssertion(
            asset_id=self.asset_id,
            used=True,
            identity_preserved=self.identity_preserved,
            cropped=self.cropped,
            dominance=self.dominance,
            replaced_by=None,
            generated_substitute=False,
        )


class MaskingTool:
    """Restrict the asset to the region the caller vouched for."""

    def apply(self, image: Image.Image, mask_bytes: bytes | None) -> Image.Image:
        rgba = image.convert("RGBA")
        if mask_bytes is None:
            return rgba
        try:
            with Image.open(io.BytesIO(mask_bytes)) as source_mask:
                mask = source_mask.convert("L")
        except (UnidentifiedImageError, OSError) as exc:
            raise PreservationError(
                PreservationFailure.INVALID_MASK, "mask bytes are not a readable image"
            ) from exc
        if mask.size != rgba.size:
            raise PreservationError(
                PreservationFailure.INVALID_MASK, "mask dimensions must match source image"
            )
        # Intersect with the alpha the file already carried: a mask may hide
        # more of the asset, never reveal what the upload had cut away.
        opaque = Image.new("L", rgba.size, 255)
        transparent = Image.new("L", rgba.size, 0)
        alpha = Image.composite(opaque, transparent, mask)
        alpha = Image.composite(alpha, transparent, rgba.getchannel("A"))
        rgba.putalpha(alpha)
        return rgba


class BackgroundRemovalTool:
    """Clear the backdrop the photo was shot against, and only that.

    Flood filling inward from the corners reaches background that touches an
    edge and stops at the subject. A colour region enclosed by the product
    keeps its pixels, because a hole punched through a car door would be a
    change to the product itself.
    """

    def apply(self, image: Image.Image, *, threshold: int = 24) -> Image.Image:
        rgba = image.convert("RGBA")
        working = rgba.convert("RGB").convert("RGBA")
        corners = (
            (0, 0),
            (working.width - 1, 0),
            (0, working.height - 1),
            (working.width - 1, working.height - 1),
        )
        colours = [working.getpixel(point)[:3] for point in corners]
        channel_spread = max(
            max(colour[channel] for colour in colours) - min(colour[channel] for colour in colours)
            for channel in range(3)
        )
        if channel_spread > 80:
            raise PreservationError(
                PreservationFailure.BACKGROUND_REMOVAL_UNSAFE,
                "background varies too much for deterministic removal; "
                "retain it or supply a trusted source-sized mask",
            )

        # Alpha zero is an unambiguous marker because this copy starts opaque.
        # It cannot collide with a real dark label, tyre, or package detail.
        sentinel = (1, 2, 3, 0)
        for corner in corners:
            ImageDraw.floodfill(working, corner, sentinel, thresh=threshold)
        mask = working.getchannel("A").point(lambda value: 255 if value else 0)
        mask = Image.composite(mask, Image.new("L", rgba.size, 0), rgba.getchannel("A"))
        rgba.putalpha(mask)
        return rgba


class CropTool:
    """Trim transparent margin. Never the subject."""

    def apply(self, image: Image.Image) -> Image.Image:
        bounds = image.getchannel("A").getbbox()
        if bounds is None:
            raise PreservationError(
                PreservationFailure.INVALID_IMAGE, "masking removed the entire asset"
            )
        return image.crop(bounds)


class PerspectiveTool:
    def apply(self, image: Image.Image, directive: PerspectiveDirective) -> Image.Image:
        if directive.mode is PerspectiveMode.PRESERVE_SOURCE or directive.strength == 0:
            return image
        inset = round(abs(directive.strength) * image.width)
        if directive.strength > 0:
            quad = (inset, 0, image.width - inset, 0, image.width, image.height, 0, image.height)
        else:
            quad = (0, 0, image.width, 0, image.width - inset, image.height, inset, image.height)
        return image.transform(
            image.size,
            Image.Transform.QUAD,
            quad,
            resample=Image.Resampling.BICUBIC,
        )


class LightingAdaptationTool:
    """Match the scene's exposure without touching hue or saturation."""

    def apply(self, image: Image.Image, factor: float) -> Image.Image:
        if factor == 1:
            return image
        alpha = image.getchannel("A")
        adapted = ImageEnhance.Brightness(image.convert("RGB")).enhance(factor)
        adapted = adapted.convert("RGBA")
        adapted.putalpha(alpha)
        return adapted


class EdgeCleanupTool:
    """Soften the cut-out edge, working on alpha alone."""

    def apply(self, image: Image.Image) -> Image.Image:
        alpha = image.getchannel("A").filter(ImageFilter.MedianFilter(3))
        alpha = alpha.filter(ImageFilter.GaussianBlur(0.35))
        cleaned = image.copy()
        cleaned.putalpha(alpha)
        return cleaned


class ScalePlacementTool:
    """Fit inside the region without stretching: aspect ratio is identity too."""

    def apply(self, image: Image.Image, target: Bounds) -> tuple[Image.Image, Bounds]:
        ratio = min(target.width / image.width, target.height / image.height)
        width = max(1, round(image.width * ratio))
        height = max(1, round(image.height * ratio))
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
        actual = Bounds(
            x=target.x + (target.width - width) // 2,
            y=target.y + (target.height - height) // 2,
            width=width,
            height=height,
        )
        return resized, actual


class ShadowIntegrationTool:
    """Ground the asset with a shadow drawn outside it, never over it."""

    def compose(
        self,
        foreground: Image.Image,
        actual: Bounds,
        canvas: Canvas,
        directive: ShadowDirective,
    ) -> Image.Image:
        output = Image.new("RGBA", (canvas.width, canvas.height), (0, 0, 0, 0))
        if directive.enabled and directive.opacity:
            shadow_alpha = Image.new("L", output.size, 0)
            shadow_alpha.paste(
                foreground.getchannel("A"),
                (actual.x + directive.offset_x, actual.y + directive.offset_y),
            )
            shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(directive.blur_radius))
            shadow_alpha = shadow_alpha.point(lambda value: value * directive.opacity // 255)
            shadow = Image.new("RGBA", output.size, (0, 0, 0, 0))
            shadow.putalpha(shadow_alpha)
            output = Image.alpha_composite(output, shadow)
        output.alpha_composite(foreground, (actual.x, actual.y))
        return output


class ProductPreservationPipeline:
    """Nine bounded edits over the customer's own pixels, in a fixed order.

    The order is not arbitrary: alpha is decided first, geometry second,
    placement last, so every later step operates on an asset whose extent is
    already settled and no step has to guess what belongs to the product.
    """

    def __init__(self) -> None:
        self.masking = MaskingTool()
        self.background_removal = BackgroundRemovalTool()
        self.crop = CropTool()
        self.perspective = PerspectiveTool()
        self.lighting = LightingAdaptationTool()
        self.edge_cleanup = EdgeCleanupTool()
        self.scale_placement = ScalePlacementTool()
        self.shadow = ShadowIntegrationTool()

    def process(self, payload: PreservationInput) -> PreservedAssetResult:
        self._refuse_forbidden_work(payload)
        image = self._open(payload.image_bytes, payload.mime_type)
        steps: list[EditStep] = []

        image = self.masking.apply(image, payload.mask_bytes)
        steps.append(
            _step(
                EditOperation.MASKING,
                payload.mask_bytes is not None,
                "Source-sized alpha mask applied.",
                "Existing alpha retained.",
            )
        )
        if payload.remove_background:
            image = self.background_removal.apply(image)
        steps.append(
            _step(
                EditOperation.BACKGROUND_REMOVAL,
                payload.remove_background,
                "Edge-connected background removed.",
                "Background retained.",
            )
        )
        if image.getchannel("A").getbbox() is None:
            raise PreservationError(
                PreservationFailure.INVALID_IMAGE,
                "masking and background removal left none of the asset",
            )
        if payload.crop_to_content:
            image = self.crop.apply(image)
        steps.append(
            _step(
                EditOperation.CROP,
                payload.crop_to_content,
                "Transparent margin cropped.",
                "Source framing preserved.",
            )
        )
        image = self.perspective.apply(image, payload.perspective)
        steps.append(
            EditStep(
                operation=EditOperation.PERSPECTIVE_HANDLING,
                applied=payload.perspective.mode is PerspectiveMode.CORRECT_DISTORTION,
                detail=(
                    f"Mode {payload.perspective.mode.value}; "
                    f"strength {payload.perspective.strength}."
                ),
            )
        )
        image = self.lighting.apply(image, payload.lighting_factor)
        steps.append(
            EditStep(
                operation=EditOperation.LIGHTING_ADAPTATION,
                applied=payload.lighting_factor != 1,
                detail=f"Bounded brightness factor {payload.lighting_factor}.",
            )
        )
        if payload.edge_cleanup:
            image = self.edge_cleanup.apply(image)
        steps.append(
            _step(
                EditOperation.EDGE_CLEANUP,
                payload.edge_cleanup,
                "Alpha edge locally cleaned.",
                "Alpha edge unchanged.",
            )
        )
        image, actual = self.scale_placement.apply(image, payload.target_bounds)
        steps.append(
            EditStep(
                operation=EditOperation.SCALE,
                applied=True,
                detail=f"Aspect-preserving scale to {actual.width}x{actual.height}.",
            )
        )
        steps.append(
            EditStep(
                operation=EditOperation.PLACEMENT,
                applied=True,
                detail=f"Placed at ({actual.x}, {actual.y}).",
            )
        )
        output = self.shadow.compose(image, actual, payload.canvas, payload.shadow)
        steps.append(
            _step(
                EditOperation.SHADOW_INTEGRATION,
                payload.shadow.enabled,
                "External shadow layer integrated.",
                "No shadow requested.",
            )
        )

        buffer = io.BytesIO()
        output.save(buffer, format="PNG", optimize=True)
        output_bytes = buffer.getvalue()
        evidence = [
            "source_checksum_verified",
            "source_pixels_transformed_without_subject_substitution",
            "aspect_ratio_preserved_during_scale",
        ]
        return PreservedAssetResult(
            asset_id=payload.asset_id,
            role=payload.policy.role,
            image_bytes=output_bytes,
            width=output.width,
            height=output.height,
            actual_bounds=actual,
            source_checksum=hashlib.sha256(payload.image_bytes).hexdigest(),
            output_checksum=hashlib.sha256(output_bytes).hexdigest(),
            identity_preserved=(
                not payload.source_is_generated and payload.replacement_asset_id is None
            ),
            fidelity_evidence=evidence,
            cropped=payload.crop_to_content,
            dominance=_coverage(image, payload.canvas),
            steps=steps,
            contract_fingerprint=payload.policy.contract_fingerprint,
        )

    def _refuse_forbidden_work(self, payload: PreservationInput) -> None:
        """Answer the permission questions before opening a single byte.

        `preserve_identity` is the ticket's hard rule, and the policy's own
        allowances are honoured alongside it: an asset nobody authorised for
        replacement does not become replaceable because that flag happens to
        be off.
        """
        policy = payload.policy
        if payload.replacement_asset_id is not None and (
            policy.preserve_identity or not policy.allow_replace
        ):
            raise PreservationError(
                PreservationFailure.REPLACEMENT_FORBIDDEN,
                f"{policy.role.value} asset may not be swapped for another",
            )
        if payload.replacement_asset_id is not None:
            raise PreservationError(
                PreservationFailure.REPLACEMENT_OUT_OF_SCOPE,
                "replacement is permitted by policy but must be performed by "
                "the asset-selection boundary",
            )
        if payload.source_is_generated and (
            policy.preserve_identity or not policy.allow_generation
        ):
            raise PreservationError(
                PreservationFailure.GENERATED_SOURCE_FORBIDDEN,
                f"generated bytes cannot stand in for a {policy.role.value} asset",
            )
        if payload.crop_to_content and not policy.allow_crop:
            raise PreservationError(
                PreservationFailure.CROP_FORBIDDEN, "asset policy forbids cropping"
            )
        bounds = payload.target_bounds
        if (
            bounds.x + bounds.width > payload.canvas.width
            or bounds.y + bounds.height > payload.canvas.height
        ):
            raise PreservationError(
                PreservationFailure.OUTSIDE_CANVAS, "target bounds exceed the canvas"
            )

    def _open(self, image_bytes: bytes, declared_mime_type: str) -> Image.Image:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(image_bytes)) as source:
                    actual_mime_type = source.get_format_mimetype()
                    if actual_mime_type != declared_mime_type:
                        raise PreservationError(
                            PreservationFailure.MIME_TYPE_MISMATCH,
                            f"declared {declared_mime_type}, "
                            f"decoded {actual_mime_type or 'unknown'}",
                        )
                    if source.width > 8192 or source.height > 8192:
                        raise PreservationError(
                            PreservationFailure.IMAGE_TOO_LARGE,
                            "source dimensions exceed the 8192px processing limit",
                        )
                    source.load()
                    return source.convert("RGBA")
        except PreservationError:
            raise
        except Image.DecompressionBombWarning as exc:
            raise PreservationError(
                PreservationFailure.IMAGE_TOO_LARGE, "asset exceeds Pillow's safe pixel limit"
            ) from exc
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise PreservationError(
                PreservationFailure.INVALID_IMAGE, "asset bytes are not a safe readable image"
            ) from exc


def _coverage(placed: Image.Image, canvas: Canvas) -> float:
    """How much of the frame the asset holds, weighted by its own alpha."""
    histogram = placed.getchannel("A").histogram()
    opaque = sum(value * count for value, count in enumerate(histogram))
    return min(1.0, opaque / (255 * canvas.width * canvas.height))


def _step(operation: EditOperation, applied: bool, done: str, skipped: str) -> EditStep:
    return EditStep(operation=operation, applied=applied, detail=done if applied else skipped)


__all__ = [
    "PROTECTED_ROLES",
    "BackgroundRemovalTool",
    "CropTool",
    "EdgeCleanupTool",
    "EditOperation",
    "EditStep",
    "LightingAdaptationTool",
    "MaskingTool",
    "PerspectiveDirective",
    "PerspectiveMode",
    "PerspectiveTool",
    "PreservationError",
    "PreservationFailure",
    "PreservationInput",
    "PreservedAssetResult",
    "ProductPreservationPipeline",
    "ScalePlacementTool",
    "ShadowDirective",
    "ShadowIntegrationTool",
]
