import json
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.providers import (
    ProviderResponseError,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

from .schemas import (
    DESIGN_CRITIC_WIRE_SCHEMA,
    DesignCriticDecision,
    DesignCriticInput,
    DesignCriticReadout,
    DesignCriticReport,
    DesignDimension,
    DesignProblem,
)

_CREATIVE_DIMENSIONS = {DesignDimension.CREATIVITY}
_ART_DIRECTION_DIMENSIONS = {
    DesignDimension.BRAND_CONSISTENCY,
    DesignDimension.FOCAL_POINT,
    DesignDimension.NEGATIVE_SPACE,
    DesignDimension.PRODUCT_DOMINANCE,
}


class SeniorDesignCritic:
    """Diagnose visible design defects instead of producing aesthetic scores."""

    def __init__(self, vision: VisionProvider) -> None:
        self._vision = vision

    async def review(
        self, payload: DesignCriticInput, *, revision_requests: int = 0
    ) -> DesignCriticReport:
        # The whole review is one call. The reasoning a vision model spends on a
        # busy render is charged per call and dwarfs the answer, so splitting the
        # dimensions across calls costs more wall time than it saves.
        preview = _inspection_preview(payload.final_image)
        readout, response = await self._reviewed(payload, preview)

        problems = [
            DesignProblem(
                dimension=check.dimension,
                problem=check.problem,
                location=check.location,
                cause=check.cause,
                severity=check.severity,
                recommended_change=check.recommended_change,
                target_stage=_target_stage(check.dimension),
            )
            for check in readout.checks
            if not check.passed
        ]
        return DesignCriticReport(
            decision=DesignCriticDecision.REVISE if problems else DesignCriticDecision.PASS,
            checks=readout.checks,
            problems=problems,
            summary=readout.summary,
            provider=response.provider,
            model=response.model,
            contract_fingerprint=payload.design_spec.contract_fingerprint,
            render_fingerprint=payload.post_draft.render_fingerprint,
            revision_requests=revision_requests,
        )

    async def _reviewed(
        self, payload: DesignCriticInput, preview: tuple[bytes, str]
    ) -> tuple[DesignCriticReadout, VisionResponse]:
        response = await self._analyze(payload, preview)
        try:
            return _ordered(response), response
        except (TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._analyze(
                payload,
                preview,
                previous_output=response.data,
                validation_error=str(first_exc),
            )
            try:
                return _ordered(repair), repair
            except (TypeError, ValueError, ValidationError) as exc:
                raise ProviderResponseError(
                    "senior design critic returned an unusable structured review"
                ) from exc

    async def _analyze(
        self,
        payload: DesignCriticInput,
        preview: tuple[bytes, str],
        *,
        previous_output: dict[str, Any] | None = None,
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
        return await self._vision.analyze(
            VisionRequest(
                image=image,
                mime_type=mime_type,
                prompt=prompt,
                response_schema=DESIGN_CRITIC_WIRE_SCHEMA,
            )
        )


def _inspection_preview(image_bytes: bytes, *, max_edge: int = 640) -> tuple[bytes, str]:
    """Create the mobile-scale review image after original checksum validation."""
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
        raise ValueError("design critic final render is not a valid image") from exc


def _target_stage(dimension: DesignDimension) -> SupervisorStage:
    if dimension in _CREATIVE_DIMENSIONS:
        return SupervisorStage.CREATIVE_CONCEPT
    if dimension in _ART_DIRECTION_DIMENSIONS:
        return SupervisorStage.ART_DIRECTION
    return SupervisorStage.DESIGN_SPEC


def _ordered(response: VisionResponse) -> DesignCriticReadout:
    """Validate the review and give it the declared dimension order."""
    readout = DesignCriticReadout.model_validate(response.data)
    checks = {check.dimension: check for check in readout.checks}
    return readout.model_copy(
        update={"checks": [checks[dimension] for dimension in DesignDimension]}
    )


def _prompt(payload: DesignCriticInput) -> str:
    contract = PostSemanticContract.from_dict(payload.semantic_contract)
    art = payload.art_direction
    spec = payload.design_spec
    context = {
        "platform": contract.platform,
        "brand": contract.brand or contract.company,
        "product": contract.product or contract.primary_entity,
        # Review needs the approved visible decisions, not upstream rationales,
        # quality checks or schema bookkeeping. Keeping this compact prevents a
        # local vision model from jumping from a 16k to a 32k context window.
        "art_direction": {
            "focal_point": art.focal_point,
            "composition": art.composition,
            # The approved ranking is what the hierarchy check is judged against.
            "visual_hierarchy": [
                {"rank": step.rank, "element": step.element.value} for step in art.visual_hierarchy
            ],
            "negative_space": art.negative_space,
            "product_dominance": art.product_dominance,
            "typography": art.typography_direction,
            "color": art.color_direction,
            "graphics": art.graphic_language,
            "cta": art.cta_treatment,
            "logo": art.logo_region,
        },
        "design_spec": {
            "canvas": spec.canvas.model_dump(mode="json"),
            "safe_area": spec.safe_area.model_dump(mode="json"),
            "regions": spec.regions.model_dump(mode="json"),
            "typography": [item.model_dump(mode="json") for item in spec.typography_roles],
            "colors": [item.model_dump(mode="json") for item in spec.color_system],
            "graphics": [item.model_dump(mode="json") for item in spec.graphic_elements],
        },
        "rendered_components": [
            {
                "kind": component.kind.value,
                "bounds": component.bounds.model_dump(mode="json"),
                "text": component.text,
                "detail": component.detail,
            }
            for component in payload.post_draft.components
        ],
    }
    names = [dimension.value for dimension in DesignDimension]
    # The response grammar already fixes the shape, so the prompt shows one
    # example of each verdict rather than a full skeleton - a skeleton of
    # passing checks reads as an instruction to pass everything.
    examples = {
        "passing_check": {
            "dimension": names[0],
            "passed": True,
            "evidence": "What the render visibly does right here.",
        },
        "failing_check": {
            "dimension": names[1],
            "passed": False,
            "problem": "What is wrong.",
            "location": "Where it is visible.",
            "cause": "Why the execution produced it.",
            "severity": "high",
            "recommended_change": "The smallest implementable correction.",
            "evidence": "What the render visibly does here.",
        },
    }
    return (
        "Act as a senior design critic. Diagnose visible execution against the approved design. "
        "Do not give taste, generic praise, or a beauty score.\n\n"
        f"APPROVED DESIGN CONTEXT:\n{json.dumps(context, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Return one check for each of these {len(names)} dimensions, in this order: "
        f"{', '.join(names)}. Judge each dimension on its own merits: pass what the render "
        "gets right and fail only what you can point at. Evidence must describe the visible "
        "render, not repeat intent. Keep every string under 20 words.\n\n"
        "Each check takes one of two shapes:\n"
        f"{json.dumps(examples, ensure_ascii=False)}\n\n"
        'Return one JSON object with "checks" and a "summary" string, no markdown or commentary.'
    )


__all__ = ["SeniorDesignCritic"]
