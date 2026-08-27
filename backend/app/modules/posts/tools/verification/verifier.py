import json
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from app.modules.posts.providers import (
    ProviderResponseError,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

from .policy import VerificationAssessment, decide_verification
from .schemas import (
    RENDER_READOUT_WIRE_SCHEMA,
    RenderReadout,
    VerificationDecision,
    VerificationInput,
    VerificationReport,
)

#: Wide enough that the witness can still read rendered copy. The design review
#: looks at a 640px thumbnail because it judges relationships; a gate that reads
#: words off the export cannot afford the same loss.
WITNESS_MAX_EDGE = 1_024


class HardVerificationGate:
    """Certify the final render against everything the post is contractually bound to.

    The gates are hard: this returns BLOCKED for a render that fails any one of
    them, and nothing downstream is expected to negotiate with that. A provider
    that cannot produce a usable readout raises instead of blocking - failing to
    look at a post is not evidence against it.
    """

    def __init__(self, vision: VisionProvider) -> None:
        self._vision = vision

    async def verify(self, payload: VerificationInput) -> VerificationReport:
        preview = _witness_preview(payload.final_image)
        readout, response = await self._witness(payload, preview)
        assessment = decide_verification(readout, payload=payload)
        return VerificationReport(
            decision=assessment.decision,
            checks=list(assessment.checks),
            failures=list(assessment.failures),
            reason=_reason(assessment),
            render_checksum=payload.post_draft.final_asset.checksum,
            render_fingerprint=payload.post_draft.render_fingerprint,
            contract_fingerprint=payload.contract().fingerprint,
            provider=response.provider,
            model=response.model,
        )

    async def _witness(
        self, payload: VerificationInput, preview: tuple[bytes, str]
    ) -> tuple[RenderReadout, VisionResponse]:
        response = await self._analyze(payload, preview)
        try:
            return RenderReadout.model_validate(response.data), response
        except (TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._analyze(
                payload, preview, previous_output=response.data, validation_error=str(first_exc)
            )
            try:
                return RenderReadout.model_validate(repair.data), repair
            except (TypeError, ValueError, ValidationError) as exc:
                # Nothing may be certified on evidence that could not be read.
                raise ProviderResponseError(
                    "hard verification returned an unusable render readout"
                ) from exc

    async def _analyze(
        self,
        payload: VerificationInput,
        preview: tuple[bytes, str],
        *,
        previous_output: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> VisionResponse:
        prompt = _prompt()
        if previous_output is not None:
            rejected = json.dumps(previous_output, ensure_ascii=False, sort_keys=True, default=str)
            prompt += (
                "\n\nCORRECTION PASS. Your previous answer was rejected. Return the complete "
                "corrected JSON object and nothing else."
                f"\nprevious_output: {rejected[:6_000]}"
                f"\nvalidation_error: {(validation_error or 'invalid readout')[:2_000]}"
            )
        image, mime_type = preview
        return await self._vision.analyze(
            VisionRequest(
                image=image,
                mime_type=mime_type,
                prompt=prompt,
                response_schema=RENDER_READOUT_WIRE_SCHEMA,
            )
        )


def _witness_preview(image_bytes: bytes, *, max_edge: int = WITNESS_MAX_EDGE) -> tuple[bytes, str]:
    """Downscale the export for the witness, after its checksum was validated."""
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
        raise ValueError("verification final render is not a valid image") from exc


def _prompt() -> str:
    example = json.dumps(
        {
            "visible_text": ["Every readable string, copied exactly."],
            "visible_brands": ["Each recognised brand name."],
            "depicted_products": ["Each product shown as a subject."],
            "description": "One sentence describing the finished post.",
        },
        ensure_ascii=False,
    )
    return (
        "Read this finished marketing post. Report only what is actually visible in the image. "
        "Do not judge whether it is good, correct or acceptable, and do not describe what you "
        "think it intends.\n\n"
        "  visible_text: every readable string, copied exactly as printed, including single "
        "words, prices, numbers and button labels. Empty only if the image has no readable "
        "characters.\n"
        "  visible_brands: every brand, company name, wordmark, emblem or logo identity you "
        "recognise. Empty if none is present.\n"
        "  depicted_products: every manufactured product shown as a subject of the image.\n"
        "  description: one sentence describing the post.\n\n"
        f"Answer with one JSON object in exactly this shape:\n{example}"
    )


def _reason(assessment: VerificationAssessment) -> str:
    if assessment.decision is VerificationDecision.PASS:
        return "Every hard verification gate passed for this render."
    gates = ", ".join(failure.gate.value for failure in assessment.failures)
    return f"Blocked by hard verification gates: {gates}."[:1_000]


__all__ = ["WITNESS_MAX_EDGE", "HardVerificationGate"]
