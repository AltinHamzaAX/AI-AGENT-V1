from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.modules.posts.agents.design_critic import DesignDimension
from app.modules.posts.agents.marketing_critic import MarketingDimension
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage
from app.modules.posts.tools.quality import (
    ApprovalDecision,
    QualityDimension,
    QualityScoringEngine,
    QualityScoringInput,
    QualityThresholds,
)
from app.modules.posts.tools.verification import VerificationGate

FINGERPRINT = "a" * 64
CHECKSUM = "b" * 64
RENDER = "c" * 64


def _marketing(score: int = 10) -> dict:
    reviews = []
    issues = []
    for dimension in MarketingDimension:
        failed = score < 8
        reviews.append(
            {
                "dimension": dimension.value,
                "score": score,
                "issue": "weak message" if failed else None,
                "severity": "high" if failed else None,
                "reason": "Evidence from the final render.",
                "recommended_action": "Tighten the message." if failed else None,
            }
        )
        if failed:
            issues.append(
                {
                    "dimension": dimension.value,
                    "issue": "weak message",
                    "severity": "high",
                    "reason": "Evidence from the final render.",
                    "recommended_action": "Tighten the message.",
                    "target_stage": "copywriting",
                }
            )
    return {
        "decision": "REVISE" if issues else "PASS",
        "score": score,
        "reviews": reviews,
        "issues": issues,
        "summary": "Complete marketing review.",
        "provider": "test",
        "model": "test",
        "contract_fingerprint": FINGERPRINT,
        "render_fingerprint": RENDER,
    }


def _design(failed: DesignDimension | None = None, severity: str = "high") -> dict:
    checks = []
    problems = []
    for dimension in DesignDimension:
        bad = dimension is failed
        check = {
            "dimension": dimension.value,
            "passed": not bad,
            "evidence": "Visible final-render evidence.",
        }
        if bad:
            diagnosis = {
                "problem": "The relationship is weak.",
                "location": "primary region",
                "cause": "Competing visual weight.",
                "severity": severity,
                "recommended_change": "Correct only this relationship.",
            }
            check.update(diagnosis)
            problems.append(
                {
                    "dimension": dimension.value,
                    **diagnosis,
                    "target_stage": "design_spec",
                }
            )
        checks.append(check)
    return {
        "decision": "REVISE" if problems else "PASS",
        "checks": checks,
        "problems": problems,
        "summary": "Complete design review.",
        "provider": "test",
        "model": "test",
        "contract_fingerprint": FINGERPRINT,
        "render_fingerprint": RENDER,
    }


def _creative(score: int = 10) -> dict:
    evaluation = {
        "strategy_fit": score,
        "audience_fit": score,
        "brand_fit": score,
        "originality": score,
        "clarity": score,
        "visual_potential": score,
        "platform_fit": score,
        "production_feasibility": score,
        "territory_differentiation": score,
        "claim_safety": score,
        "concept_hook_alignment": score,
        "weakness": "A deliberate test tradeoff.",
    }
    return {
        "contract_fingerprint": FINGERPRINT,
        "winning_concept": {"candidate_id": "idea_1"},
        "big_idea_candidates": [{"id": "idea_1", "evaluation": evaluation}],
    }


def _verification(failed: VerificationGate | None = None) -> dict:
    checks = [
        {
            "gate": gate.value,
            "passed": gate is not failed,
            "detail": "Deterministic gate evidence.",
        }
        for gate in VerificationGate
    ]
    failures = (
        [{"gate": failed.value, "detail": "Gate failed.", "evidence": ["witness"]}]
        if failed
        else []
    )
    return {
        "decision": "BLOCKED" if failed else "PASS",
        "checks": checks,
        "failures": failures,
        "reason": "Hard-gate result.",
        "render_checksum": CHECKSUM,
        "render_fingerprint": RENDER,
        "contract_fingerprint": FINGERPRINT,
    }


def _input(**changes) -> QualityScoringInput:
    values = {
        "marketing_report": _marketing(),
        "design_report": _design(),
        "creative_direction": _creative(9),
        "verification_report": _verification(),
        "render_checksum": CHECKSUM,
        "contract_fingerprint": FINGERPRINT,
    }
    values.update(changes)
    return QualityScoringInput(**values)


def test_pass_requires_overall_critical_dimensions_and_every_hard_gate() -> None:
    report = QualityScoringEngine().score(_input())

    assert report.decision is ApprovalDecision.PASS
    assert report.overall_score >= 9
    assert len(report.scores) == len(QualityDimension)
    assert all(item.passed for item in report.scores)
    assert report.failed_hard_gates == []


def test_marketing_failure_routes_to_mutation() -> None:
    report = QualityScoringEngine().score(
        _input(marketing_report=_marketing(7), thresholds=QualityThresholds(overall_minimum=7))
    )
    assert report.decision is ApprovalDecision.MUTATE
    assert QualityDimension.MARKETING_EFFECTIVENESS in report.failed_dimensions


def test_visual_failure_routes_to_recomposition() -> None:
    report = QualityScoringEngine().score(_input(design_report=_design(DesignDimension.HIERARCHY)))
    assert report.decision is ApprovalDecision.RECOMPOSE
    assert QualityDimension.VISUAL_HIERARCHY in report.failed_dimensions


def test_weak_concept_routes_to_regeneration() -> None:
    report = QualityScoringEngine().score(
        _input(
            creative_direction=_creative(7),
            thresholds=QualityThresholds(
                overall_minimum=7,
                critical_minimum=8,
                dimension_minimum=8,
                critical_dimensions={QualityDimension.PRODUCT_FIDELITY},
            ),
        )
    )
    assert report.decision is ApprovalDecision.REGENERATE


def test_hard_gate_cannot_be_outvoted_by_scores() -> None:
    report = QualityScoringEngine().score(
        _input(verification_report=_verification(VerificationGate.ASSET_FIDELITY))
    )
    assert report.decision is ApprovalDecision.REJECT
    assert all(
        item.score >= 9
        for item in report.scores
        if item.dimension is not QualityDimension.PRODUCT_FIDELITY
    )
    assert report.failed_hard_gates == [VerificationGate.ASSET_FIDELITY.value]


def test_overall_threshold_is_enforced_even_when_dimensions_pass() -> None:
    report = QualityScoringEngine().score(
        _input(
            marketing_report=_marketing(9),
            creative_direction=_creative(9),
            thresholds=QualityThresholds(overall_minimum=9.8),
        )
    )
    assert report.decision is ApprovalDecision.MUTATE
    assert report.failed_dimensions == []


def test_reports_for_different_renders_are_refused() -> None:
    marketing = deepcopy(_marketing())
    marketing["render_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="different renders"):
        QualityScoringEngine().score(_input(marketing_report=marketing))


def test_invalid_threshold_configuration_is_refused() -> None:
    with pytest.raises(ValidationError, match="critical_minimum"):
        QualityThresholds(critical_minimum=7, dimension_minimum=8)


def test_quality_stage_runs_after_both_critic_layers() -> None:
    policy = DEFAULT_SUPERVISOR_PLAN.get(SupervisorStage.QUALITY_SCORING)
    assert policy.dependencies == (SupervisorStage.DESIGN_REVIEW,)
    assert policy.output_sections[0].value == "quality_approval"


def test_creative_evidence_must_match_the_contract() -> None:
    creative = _creative(9)
    creative["contract_fingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="semantic contract"):
        QualityScoringEngine().score(_input(creative_direction=creative))
