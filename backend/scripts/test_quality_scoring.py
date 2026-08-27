"""Manually run Ticket 40 against a persisted Posts workflow state."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.modules.posts.agents.design_critic import DesignDimension
from app.modules.posts.agents.marketing_critic import MarketingDimension
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.tools.composition import PostDraft
from app.modules.posts.tools.quality import (
    QualityScoringEngine,
    QualityScoringInput,
    QualityThresholds,
)
from app.modules.posts.tools.verification import VerificationGate


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state", type=Path, help="Persisted workflow-state JSON")
    source.add_argument("--demo", action="store_true", help="Use built-in deterministic evidence")
    parser.add_argument("--weak", action="store_true", help="Fail visual hierarchy in demo mode")
    parser.add_argument("--hard-fail", action="store_true", help="Fail asset fidelity in demo mode")
    parser.add_argument("--output", type=Path, default=Path("tmp/quality-scoring-report.json"))
    parser.add_argument("--overall", type=float, default=9.0)
    parser.add_argument("--critical", type=float, default=8.5)
    parser.add_argument("--dimension", type=float, default=8.0)
    return parser.parse_args()


def _object(state: dict, section: PostWorkflowSection) -> dict:
    value = state.get(section.value)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"workflow state has no populated '{section.value}' section")
    return value


def _demo_input(args: argparse.Namespace, thresholds: QualityThresholds) -> QualityScoringInput:
    fingerprint = "a" * 64
    checksum = "b" * 64
    render = "c" * 64
    marketing_reviews = [
        {
            "dimension": dimension.value,
            "score": 10,
            "reason": "The approved message is clear and aligned in the demo render.",
        }
        for dimension in MarketingDimension
    ]
    design_checks = []
    design_problems = []
    for dimension in DesignDimension:
        failed = args.weak and dimension is DesignDimension.HIERARCHY
        check = {
            "dimension": dimension.value,
            "passed": not failed,
            "evidence": "Deterministic visual evidence from the demo render.",
        }
        if failed:
            diagnosis = {
                "problem": "Headline and CTA compete for primary attention.",
                "location": "headline and CTA regions",
                "cause": "Similar scale and contrast create two focal points.",
                "severity": "high",
                "recommended_change": "Reduce CTA weight and preserve headline primacy.",
            }
            check.update(diagnosis)
            design_problems.append(
                {"dimension": dimension.value, **diagnosis, "target_stage": "design_spec"}
            )
        design_checks.append(check)

    failed_gate = VerificationGate.ASSET_FIDELITY if args.hard_fail else None
    gate_checks = [
        {
            "gate": gate.value,
            "passed": gate is not failed_gate,
            "detail": "Deterministic hard-gate evidence from the demo render.",
        }
        for gate in VerificationGate
    ]
    gate_failures = (
        [
            {
                "gate": failed_gate.value,
                "detail": "Protected product pixels differ from the approved original.",
                "evidence": ["demo asset checksum mismatch"],
            }
        ]
        if failed_gate
        else []
    )
    evaluation = {
        "strategy_fit": 9,
        "audience_fit": 9,
        "brand_fit": 9,
        "originality": 9,
        "clarity": 9,
        "visual_potential": 9,
        "platform_fit": 9,
        "production_feasibility": 9,
        "territory_differentiation": 9,
        "claim_safety": 9,
        "concept_hook_alignment": 9,
        "weakness": "The concept depends on disciplined visual restraint.",
    }
    return QualityScoringInput(
        marketing_report={
            "decision": "PASS",
            "score": 10,
            "reviews": marketing_reviews,
            "issues": [],
            "summary": "The demo draft satisfies every marketing dimension.",
            "provider": "scripted/demo",
            "model": "deterministic",
            "contract_fingerprint": fingerprint,
            "render_fingerprint": render,
        },
        design_report={
            "decision": "REVISE" if design_problems else "PASS",
            "checks": design_checks,
            "problems": design_problems,
            "summary": "The demo draft has one hierarchy defect."
            if design_problems
            else "The demo draft satisfies every design dimension.",
            "provider": "scripted/demo",
            "model": "deterministic",
            "contract_fingerprint": fingerprint,
            "render_fingerprint": render,
        },
        creative_direction={
            "contract_fingerprint": fingerprint,
            "winning_concept": {"candidate_id": "idea_1"},
            "big_idea_candidates": [{"id": "idea_1", "evaluation": evaluation}],
        },
        verification_report={
            "decision": "BLOCKED" if gate_failures else "PASS",
            "checks": gate_checks,
            "failures": gate_failures,
            "reason": "A hard gate failed." if gate_failures else "Every hard gate passed.",
            "render_checksum": checksum,
            "render_fingerprint": render,
            "contract_fingerprint": fingerprint,
        },
        render_checksum=checksum,
        contract_fingerprint=fingerprint,
        thresholds=thresholds,
    )


def _state_input(args: argparse.Namespace, thresholds: QualityThresholds) -> QualityScoringInput:
    assert args.state is not None
    document = json.loads(args.state.read_text(encoding="utf-8"))
    state = document.get("state", document) if isinstance(document, dict) else document
    if not isinstance(state, dict):
        raise ValueError("state file must contain a workflow-state JSON object")
    draft = PostDraft.model_validate(_object(state, PostWorkflowSection.POST_DRAFT))
    return QualityScoringInput(
        marketing_report=_object(state, PostWorkflowSection.QUALITY),
        design_report=_object(state, PostWorkflowSection.DESIGN_QUALITY),
        creative_direction=_object(state, PostWorkflowSection.CREATIVE_CONCEPT),
        verification_report=_object(state, PostWorkflowSection.VERIFICATION),
        render_checksum=draft.final_asset.checksum,
        contract_fingerprint=draft.contract_fingerprint,
        thresholds=thresholds,
    )


def main() -> int:
    args = _arguments()
    try:
        if (args.weak or args.hard_fail) and not args.demo:
            raise ValueError("--weak and --hard-fail require --demo")
        if args.weak and args.hard_fail:
            raise ValueError("choose only one demo failure mode")
        thresholds = QualityThresholds(
            overall_minimum=args.overall,
            critical_minimum=args.critical,
            dimension_minimum=args.dimension,
        )
        payload = _demo_input(args, thresholds) if args.demo else _state_input(args, thresholds)
        report = QualityScoringEngine().score(payload)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"DECISION: {report.decision.value}")
    print(f"Overall:  {report.overall_score}/10 (minimum {report.thresholds.overall_minimum})")
    print(f"Report:   {args.output.resolve()}")
    for item in report.scores:
        status = "PASS" if item.passed else "FAIL"
        marker = " critical" if item.critical else ""
        print(f"[{status}] {item.dimension.value:<25} {item.score}/{item.threshold}{marker}")
    if report.failed_hard_gates:
        print("Hard gates: " + ", ".join(report.failed_hard_gates))
    if report.recommended_action:
        print("Action: " + report.recommended_action)
    return 0 if report.decision.value == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
