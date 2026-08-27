import json
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.providers import (
    ProviderResponseError,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

from .schemas import (
    VISION_CRITIC_WIRE_SCHEMA,
    VisionCriticDecision,
    VisionCriticInput,
    VisionCriticReadout,
    VisionCriticReport,
    VisionDimension,
)

VISION_PREVIEW_MAX_EDGE = 1024


class VisionCritic:
    """Compare the approved design intent with pixels in the final render."""

    def __init__(self, vision: VisionProvider) -> None:
        self._vision = vision

    async def review(
        self, payload: VisionCriticInput, *, revision_requests: int = 0
    ) -> VisionCriticReport:
        preview = _preview(payload.final_image)
        readout, response = await self._reviewed(payload, preview)
        return VisionCriticReport(
            decision=VisionCriticDecision.REVISE if readout.issues else VisionCriticDecision.PASS,
            assessed_dimensions=readout.assessed_dimensions,
            issues=readout.issues,
            summary=readout.summary,
            provider=response.provider,
            model=response.model,
            contract_fingerprint=payload.design_spec.contract_fingerprint,
            render_fingerprint=payload.post_draft.render_fingerprint,
            render_checksum=payload.post_draft.final_asset.checksum,
            revision_requests=revision_requests,
        )

    async def _reviewed(self, payload, preview) -> tuple[VisionCriticReadout, VisionResponse]:
        response = await self._analyze(payload, preview)
        try:
            return _ordered(response), response
        except (TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._analyze(
                payload, preview, previous_output=response.data, validation_error=str(first_exc)
            )
            try:
                return _ordered(repair), repair
            except (TypeError, ValueError, ValidationError) as exc:
                raise ProviderResponseError("vision critic returned an unusable review") from exc

    async def _analyze(
        self, payload, preview, *, previous_output: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> VisionResponse:
        prompt = _prompt(payload)
        if previous_output is not None:
            prompt += (
                "\n\nCORRECTION PASS. Return the complete corrected JSON object only."
                f"\nprevious_output: {json.dumps(previous_output, default=str)[:10_000]}"
                f"\nvalidation_error: {(validation_error or 'invalid review')[:2_000]}"
            )
        image, mime_type = preview
        return await self._vision.analyze(VisionRequest(
            image=image, mime_type=mime_type, prompt=prompt,
            response_schema=VISION_CRITIC_WIRE_SCHEMA,
        ))


def _preview(image_bytes: bytes, *, max_edge: int = VISION_PREVIEW_MAX_EDGE) -> tuple[bytes, str]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            if max(image.size) <= max_edge:
                return image_bytes, Image.MIME.get(image.format, "image/png")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG", optimize=True)
            return output.getvalue(), "image/png"
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("vision critic final render is not a valid image") from exc


def _ordered(response: VisionResponse) -> VisionCriticReadout:
    readout = VisionCriticReadout.model_validate(response.data)
    return readout.model_copy(update={"assessed_dimensions": list(VisionDimension)})


def _prompt(payload: VisionCriticInput) -> str:
    contract = PostSemanticContract.from_dict(payload.semantic_contract)
    expected = {
        "brand": contract.brand or contract.company,
        "product": contract.product or contract.primary_entity,
        "platform": contract.platform,
        "approved_copy": {
            key: value for key, value in payload.copy_draft.model_dump(mode="json").items()
            if isinstance(value, str) and value
        },
        "design_spec": payload.design_spec.model_dump(mode="json"),
        "asset_policies": [policy.model_dump(mode="json") for policy in payload.asset_policies],
        "rendered_components": [
            {
                "kind": item.kind.value, "bounds": item.bounds.model_dump(mode="json"),
                "text": item.text, "detail": item.detail,
            }
            for item in payload.post_draft.components
        ],
    }
    dimensions = [item.value for item in VisionDimension]
    return (
        "Act as a visual-perception critic. Inspect the actual pixels of this final marketing "
        "render and compare EXPECTED design intent with OBSERVED visual reality. Metadata is "
        "expectation, never proof of what rendered. Report only visible, localized failures; "
        "do not invent issues and do not give aesthetic scores. Check actual hierarchy, "
        "readability, product and logo fidelity, awkward crops, overlaps, spacing, distortion, "
        "AI artifacts, balance, focal point, subject scale, CTA visibility and text legibility. "
        "Confidence is perceptual certainty from 0 to 1. Critical means identity is wrong or "
        "content is unusable; high materially breaks the message; medium is clearly defective; "
        "low is minor polish.\n\n"
        f"EXPECTED CONTEXT:\n{json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Assess each dimension exactly once in this order: {', '.join(dimensions)}. Include all "
        "names in assessed_dimensions. For each visible failure output issue, region, severity, "
        "confidence, expected, observed, and the smallest implementable recommended_action. "
        "Return JSON only."
    )


__all__ = ["VISION_PREVIEW_MAX_EDGE", "VisionCritic"]
