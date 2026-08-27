from collections.abc import Iterable
from typing import Any

from app.modules.posts.agents.design_critic import DesignCriticReport, DesignDimension
from app.modules.posts.agents.marketing_critic import MarketingCriticReport, MarketingDimension
from app.modules.posts.tools.verification import VerificationDecision, VerificationReport

from .schemas import (
    ApprovalDecision,
    QualityApprovalReport,
    QualityDimension,
    QualityScore,
    QualityScoringInput,
)

_DESIGN_SCORE = {None: 10.0, "low": 7.5, "medium": 6.5, "high": 5.0, "critical": 2.0}
_RECOMPOSE = {
    QualityDimension.COMPOSITION,
    QualityDimension.VISUAL_HIERARCHY,
    QualityDimension.TYPOGRAPHY,
    QualityDimension.COLOR,
    QualityDimension.BRAND_FIT,
    QualityDimension.PRODUCT_FIDELITY,
    QualityDimension.PLATFORM_FIT,
    QualityDimension.OVERALL_POLISH,
}
_REGENERATE = {QualityDimension.CREATIVE_CONCEPT, QualityDimension.DIFFERENTIATION}


class QualityScoringEngine:
    """Deterministically aggregate upstream evidence; never invent a new opinion."""

    def score(self, payload: QualityScoringInput) -> QualityApprovalReport:
        marketing = MarketingCriticReport.model_validate(payload.marketing_report)
        design = DesignCriticReport.model_validate(payload.design_report)
        verification = VerificationReport.model_validate(payload.verification_report)
        self._validate_identity(payload, marketing, design, verification)
        raw = self._raw_scores(marketing, design, payload.creative_direction, verification)
        scores = [
            self._score(dimension, *raw[dimension], payload=payload)
            for dimension in QualityDimension
        ]
        total_weight = sum(payload.thresholds.weight_for(item.dimension) for item in scores)
        overall = round(
            sum(item.score * payload.thresholds.weight_for(item.dimension) for item in scores)
            / total_weight,
            2,
        )
        hard_failures = [failure.gate.value for failure in verification.failures]
        failed = [item.dimension for item in scores if not item.passed]
        decision = self._decision(overall, failed, hard_failures, payload)
        return QualityApprovalReport(
            decision=decision,
            overall_score=overall,
            scores=scores,
            failed_dimensions=failed,
            failed_hard_gates=hard_failures,
            reason=self._reason(decision, overall, failed, hard_failures, payload),
            recommended_action=self._action(decision, failed),
            thresholds=payload.thresholds,
            render_checksum=payload.render_checksum,
            contract_fingerprint=payload.contract_fingerprint,
        )

    @staticmethod
    def _validate_identity(payload, marketing, design, verification) -> None:
        creative_fingerprint = payload.creative_direction.get("contract_fingerprint")
        if {
            marketing.contract_fingerprint,
            design.contract_fingerprint,
            verification.contract_fingerprint,
            creative_fingerprint,
        } != {payload.contract_fingerprint}:
            raise ValueError("quality evidence disagrees on the semantic contract")
        if {
            marketing.render_fingerprint,
            design.render_fingerprint,
            verification.render_fingerprint,
        } != {verification.render_fingerprint}:
            raise ValueError("quality evidence describes different renders")
        if verification.render_checksum != payload.render_checksum:
            raise ValueError("verification does not certify the scored render")

    def _raw_scores(self, marketing, design, creative: dict[str, Any], verification):
        mr = {item.dimension: item for item in marketing.reviews}
        dc = {item.dimension: item for item in design.checks}

        def market(*dims):
            values = [mr[item] for item in dims]
            return (
                self._average(item.score for item in values),
                [item.reason for item in values],
                [f"marketing:{item.dimension.value}" for item in values],
            )

        def visual(dim):
            item = dc[dim]
            return (
                _DESIGN_SCORE[item.severity.value if item.severity else None],
                [item.evidence],
                [f"design:{dim.value}"],
            )

        selected = creative.get("winning_concept", {}).get("candidate_id")
        candidates = creative.get("big_idea_candidates", [])
        candidate = next((item for item in candidates if item.get("id") == selected), None)
        if not candidate or not isinstance(candidate.get("evaluation"), dict):
            raise ValueError("quality scoring requires the selected creative evaluation")
        ce = candidate["evaluation"]

        product = 10.0 if verification.decision is VerificationDecision.PASS else 1.0
        return {
            QualityDimension.MARKETING_EFFECTIVENESS: market(
                MarketingDimension.OBJECTIVE_ALIGNMENT,
                MarketingDimension.POSITIONING,
                MarketingDimension.MESSAGE_CLARITY,
                MarketingDimension.USP_VALUE_PROPOSITION,
                MarketingDimension.CTA,
            ),
            QualityDimension.CREATIVE_CONCEPT: (
                self._average([ce["strategy_fit"], ce["clarity"], ce["visual_potential"]]),
                ["Selected concept scorecard: strategy fit, clarity and visual potential."],
                ["creative_direction:evaluation"],
            ),
            QualityDimension.COMPOSITION: visual(DesignDimension.COMPOSITION),
            QualityDimension.VISUAL_HIERARCHY: visual(DesignDimension.HIERARCHY),
            QualityDimension.TYPOGRAPHY: visual(DesignDimension.TYPOGRAPHY),
            QualityDimension.COLOR: visual(DesignDimension.COLOR),
            QualityDimension.BRAND_FIT: (
                self._average([ce["brand_fit"], visual(DesignDimension.BRAND_CONSISTENCY)[0]]),
                ["Creative brand fit and rendered brand consistency."],
                ["creative_direction:brand_fit", "design:brand_consistency"],
            ),
            QualityDimension.PRODUCT_FIDELITY: (
                product,
                ["Hard verification asset/product fidelity gates."],
                ["verification"],
            ),
            QualityDimension.AUDIENCE_FIT: (
                self._average(
                    [ce["audience_fit"], mr[MarketingDimension.AUDIENCE_RELEVANCE].score]
                ),
                [mr[MarketingDimension.AUDIENCE_RELEVANCE].reason],
                ["creative_direction:audience_fit", "marketing:audience_relevance"],
            ),
            QualityDimension.PLATFORM_FIT: (
                self._average(
                    [
                        ce["platform_fit"],
                        visual(DesignDimension.PLATFORM_FIT)[0],
                        visual(DesignDimension.MOBILE_READABILITY)[0],
                    ]
                ),
                ["Concept, platform and mobile review evidence."],
                [
                    "creative_direction:platform_fit",
                    "design:platform_fit",
                    "design:mobile_readability",
                ],
            ),
            QualityDimension.DIFFERENTIATION: (
                self._average([ce["originality"], ce["territory_differentiation"]]),
                ["Selected concept originality and territory differentiation."],
                ["creative_direction:evaluation"],
            ),
            QualityDimension.OVERALL_POLISH: (
                self._average(
                    [
                        visual(DesignDimension.POLISH)[0],
                        visual(DesignDimension.SPACING)[0],
                        visual(DesignDimension.ALIGNMENT)[0],
                        visual(DesignDimension.CONTRAST)[0],
                        visual(DesignDimension.BALANCE)[0],
                        visual(DesignDimension.FOCAL_POINT)[0],
                        visual(DesignDimension.NEGATIVE_SPACE)[0],
                    ]
                ),
                ["Rendered craft checks contributing to final polish."],
                ["design:craft_checks"],
            ),
        }

    @staticmethod
    def _average(values: Iterable[float]) -> float:
        items = list(values)
        return round(sum(items) / len(items), 2)

    @staticmethod
    def _score(dimension, score, evidence, source, *, payload):
        critical = dimension in payload.thresholds.critical_dimensions
        threshold = (
            payload.thresholds.critical_minimum
            if critical
            else payload.thresholds.dimension_minimum
        )
        return QualityScore(
            dimension=dimension,
            score=score,
            threshold=threshold,
            critical=critical,
            passed=score >= threshold,
            evidence=evidence,
            source=source,
        )

    @staticmethod
    def _decision(overall, failed, hard_failures, payload):
        if hard_failures:
            return ApprovalDecision.REJECT
        if not failed and overall >= payload.thresholds.overall_minimum:
            return ApprovalDecision.PASS
        if any(item in _REGENERATE for item in failed):
            return ApprovalDecision.REGENERATE
        if any(item in _RECOMPOSE for item in failed):
            return ApprovalDecision.RECOMPOSE
        return ApprovalDecision.MUTATE

    @staticmethod
    def _action(decision, failed):
        if decision is ApprovalDecision.PASS:
            return None
        names = ", ".join(item.value for item in failed) or "hard gates"
        verbs = {
            ApprovalDecision.REJECT: "Reject the render and resolve its hard-gate failures",
            ApprovalDecision.RECOMPOSE: "Recompose only the failing visual dimensions",
            ApprovalDecision.REGENERATE: "Regenerate the creative scene/concept evidence",
            ApprovalDecision.MUTATE: "Mutate only the failing strategy or copy dimensions",
        }
        return f"{verbs[decision]}: {names}."

    @staticmethod
    def _reason(decision, overall, failed, hard, payload):
        if hard:
            return "REJECT: hard verification gates failed: " + ", ".join(hard)
        if decision is ApprovalDecision.PASS:
            return f"PASS: overall {overall:.2f} and every dimension met configured thresholds."
        names = ", ".join(item.value for item in failed)
        threshold = payload.thresholds.overall_minimum
        failure_label = names or "overall score"
        return (
            f"{decision.value}: overall {overall:.2f}/{threshold:.2f}; "
            f"failing dimensions: {failure_label}."
        )


__all__ = ["QualityScoringEngine"]
