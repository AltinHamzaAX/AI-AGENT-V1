import json
from typing import Any

from pydantic import ValidationError

from app.modules.posts.providers import (
    ProviderResponseError,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

from .policy import decide_scene_purity
from .schemas import (
    ContaminationKind,
    ScenePurityInput,
    ScenePurityReport,
    SceneReadout,
)

_KIND_BRIEF: dict[ContaminationKind, str] = {
    ContaminationKind.FAKE_TEXT: (
        "any rendered letters, words, numbers, prices, slogans or captions, including "
        "unreadable pseudo-text on signs, packaging or screens"
    ),
    ContaminationKind.FAKE_LOGO: (
        "any drawn brand mark, emblem, monogram, badge or wordmark, real or invented"
    ),
    ContaminationKind.WATERMARK: (
        "stock-library or provider watermarks, signatures, timestamps and copyright overlays"
    ),
    ContaminationKind.AI_ARTIFACT: (
        "synthesis failures such as extra or fused limbs and fingers, impossible joins, "
        "smeared textures and nonsense detail"
    ),
    ContaminationKind.DUPLICATE_OBJECT: (
        "the same object, person or motif repeated when only one belongs in the scene"
    ),
    ContaminationKind.WRONG_PRODUCT: (
        "a manufactured product depicted as the subject of the scene"
    ),
    ContaminationKind.UNEXPECTED_BRAND: (
        "identity belonging to a company other than the one this post is for"
    ),
    ContaminationKind.DISTORTION: (
        "warped, melted or structurally impossible geometry, and broken perspective"
    ),
    ContaminationKind.UNWANTED_UI: (
        "interface chrome such as browser frames, phone bezels, buttons, cursors, "
        "app bars, sliders or dialogs"
    ),
}


class ScenePurityInspector:
    """Certify that a generated plate is clean enough to enter composition."""

    def __init__(self, vision: VisionProvider) -> None:
        self._vision = vision

    async def inspect(
        self, payload: ScenePurityInput, *, regeneration_requests: int = 0
    ) -> ScenePurityReport:
        response = await self._analyze(payload)
        try:
            readout = SceneReadout.model_validate(response.data)
        except (TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._analyze(
                payload, previous_output=response.data, validation_error=str(first_exc)
            )
            try:
                readout = SceneReadout.model_validate(repair.data)
            except (TypeError, ValueError, ValidationError) as exc:
                # Purity cannot be certified, so nothing may be certified: the
                # composition gate refuses a scene without a passing report.
                raise ProviderResponseError(
                    "scene purity inspection returned an unusable readout"
                ) from exc
            response = repair
        assessment = decide_scene_purity(readout, payload=payload)
        return ScenePurityReport(
            verdict=assessment.verdict,
            inspected=True,
            checks=list(assessment.checks),
            findings=list(assessment.findings),
            scene_checksum=payload.scene_checksum,
            scene_storage_key=payload.scene_storage_key,
            regeneration_requests=regeneration_requests,
            reason=_reason(assessment),
            provider=response.provider,
            model=response.model,
            contract_fingerprint=payload.contract().fingerprint,
        )

    async def _analyze(
        self,
        payload: ScenePurityInput,
        *,
        previous_output: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> VisionResponse:
        prompt = _prompt()
        if previous_output is not None:
            rejected = json.dumps(
                previous_output, ensure_ascii=False, sort_keys=True, default=str
            )
            prompt += (
                "\n\nCORRECTION PASS. Your previous answer was rejected. Return the complete "
                "corrected JSON object and nothing else."
                f"\nprevious_output: {rejected[:6_000]}"
                f"\nvalidation_error: {(validation_error or 'invalid readout')[:2_000]}"
            )
        return await self._vision.analyze(
            VisionRequest(
                image=payload.scene_image,
                mime_type=payload.scene_mime_type,
                prompt=prompt,
            )
        )


def _prompt() -> str:
    schema = json.dumps(SceneReadout.model_json_schema(), sort_keys=True)
    kinds = " ".join(f"- {kind.value}: {brief}." for kind, brief in _KIND_BRIEF.items())
    return (
        "You are inspecting a generated background plate for a marketing post. The plate is "
        "only scenery: the product, logo, headline, offer and call to action are composited "
        "separately from approved originals, so none of them may appear in this image. "
        "Report what you actually observe. Do not decide whether the plate is acceptable and "
        "do not describe intent. For every contamination kind give a confidence between 0 and "
        "1 that it is PRESENT in the image, plus one short sentence of evidence naming what "
        f"you saw or why it is absent. Kinds: {kinds} "
        "Transcribe every legible string into visible_text exactly as written, even single "
        "words on signage or packaging; use an empty list only when the image contains no "
        "readable characters at all. List every brand or trademark you recognise in "
        "visible_brands, and every manufactured product depicted as a subject in "
        "depicted_products, naming each in plain words. Return exactly one JSON object, no "
        f"markdown fence and no commentary. Schema: {schema}"
    )


def _reason(assessment) -> str:
    if not assessment.findings:
        return "Generated scene is clean; every contamination check passed."
    kinds = ", ".join(finding.kind.value for finding in assessment.findings)
    return f"Generated scene is contaminated and must be regenerated: {kinds}."[:1_000]


__all__ = ["ScenePurityInspector"]
