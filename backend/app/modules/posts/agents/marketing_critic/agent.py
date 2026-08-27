import json
from typing import Any

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
    MARKETING_PASS_SCORE,
    MarketingCriticDecision,
    MarketingCriticInput,
    MarketingCriticReadout,
    MarketingCriticReport,
    MarketingDimension,
    MarketingIssue,
)

_COPY_DIMENSIONS = {
    MarketingDimension.MESSAGE_CLARITY,
    MarketingDimension.SINGLE_MINDED_MESSAGE,
    MarketingDimension.CTA,
}


class MarketingCriticAgent:
    """Review the rendered post against its approved marketing strategy."""

    def __init__(self, vision: VisionProvider) -> None:
        self._vision = vision

    async def review(
        self, payload: MarketingCriticInput, *, revision_requests: int = 0
    ) -> MarketingCriticReport:
        response = await self._analyze(payload)
        try:
            readout = MarketingCriticReadout.model_validate(response.data)
        except (TypeError, ValueError, ValidationError) as first_exc:
            repair = await self._analyze(
                payload,
                previous_output=response.data,
                validation_error=str(first_exc),
            )
            try:
                readout = MarketingCriticReadout.model_validate(repair.data)
            except (TypeError, ValueError, ValidationError) as exc:
                raise ProviderResponseError(
                    "marketing critic returned an unusable structured review"
                ) from exc
            response = repair

        issues = [
            MarketingIssue(
                dimension=review.dimension,
                issue=review.issue or "Marketing criterion failed.",
                severity=review.severity,
                reason=review.reason,
                recommended_action=review.recommended_action or "Correct the failed criterion.",
                target_stage=(
                    SupervisorStage.COPYWRITING
                    if review.dimension in _COPY_DIMENSIONS
                    else SupervisorStage.MARKETING_STRATEGY
                ),
            )
            for review in readout.reviews
            if review.score < MARKETING_PASS_SCORE
        ]
        score = round(sum(review.score for review in readout.reviews) / len(readout.reviews), 2)
        return MarketingCriticReport(
            decision=(MarketingCriticDecision.REVISE if issues else MarketingCriticDecision.PASS),
            score=score,
            reviews=readout.reviews,
            issues=issues,
            summary=readout.summary,
            provider=response.provider,
            model=response.model,
            contract_fingerprint=payload.strategy.contract_fingerprint,
            render_fingerprint=payload.post_draft.render_fingerprint,
            revision_requests=revision_requests,
        )

    async def _analyze(
        self,
        payload: MarketingCriticInput,
        *,
        previous_output: dict[str, Any] | None = None,
        validation_error: str | None = None,
    ) -> VisionResponse:
        prompt = _prompt(payload)
        if previous_output is not None:
            prompt += (
                "\n\nCORRECTION PASS. Return the complete corrected JSON object only."
                f"\nprevious_output: {json.dumps(previous_output, default=str)[:8_000]}"
                f"\nvalidation_error: {(validation_error or 'invalid review')[:2_000]}"
            )
        return await self._vision.analyze(
            VisionRequest(
                image=payload.final_image,
                mime_type=payload.final_mime_type,
                prompt=prompt,
            )
        )


def _prompt(payload: MarketingCriticInput) -> str:
    contract = PostSemanticContract.from_dict(payload.semantic_contract)
    context = {
        "objective": contract.goal,
        "audience": contract.audience,
        "offer": contract.offer,
        "cta_intent": contract.cta_intent,
        "platform": contract.platform,
        "positioning": payload.strategy.positioning.decision,
        "usp": payload.strategy.usp.decision,
        "value_proposition": payload.strategy.value_proposition.decision,
        "single_minded_message": payload.strategy.single_minded_message.decision,
        "cta_strategy": payload.strategy.cta_strategy.decision,
        "copy": payload.copy_draft.model_dump(mode="json", exclude={"quality"}),
        "rendered_components": [
            {
                "kind": component.kind.value,
                "text": component.text,
                "detail": component.detail,
            }
            for component in payload.post_draft.components
        ],
    }
    dimensions = [dimension.value for dimension in MarketingDimension]
    shape = {
        "reviews": [
            {
                "dimension": dimension,
                "score": 8,
                "issue": None,
                "severity": None,
                "reason": "Evidence-based explanation comparing draft and strategy.",
                "recommended_action": None,
            }
            for dimension in dimensions
        ],
        "summary": "A specific overall judgement, not a restatement of the score.",
    }
    return (
        "Act as a senior marketing critic. Inspect the final rendered post and compare it with "
        "the approved context below. Evaluate effectiveness, not visual craft or image purity. "
        "Do not rewrite the strategy and do not reward polish when the marketing message "
        "is weak.\n\n"
        f"APPROVED CONTEXT:\n{json.dumps(context, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Review exactly these eight dimensions: {', '.join(dimensions)}. Score each 1-10. "
        f"A score below {MARKETING_PASS_SCORE} is a failure and MUST include a concrete issue, "
        "severity (low, medium, high, critical), reason tied to visible/approved evidence, and "
        "the smallest recommended action. A passing score MUST use null for issue, severity and "
        "recommended_action. Do not return only a numeric score.\n\n"
        "Return exactly one JSON object, no markdown or commentary, in this complete shape:\n"
        f"{json.dumps(shape, ensure_ascii=False)}"
    )


__all__ = ["MarketingCriticAgent"]
