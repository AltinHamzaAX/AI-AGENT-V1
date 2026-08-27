"""Manually exercise Ticket 41 targeted revision routing."""

import argparse
import json
import sys
from pathlib import Path

from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.tools.revision import RevisionDirector, RevisionFinding, RevisionRoute


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--route",
        choices=[item.value for item in RevisionRoute] + ["all"],
        default="all",
    )
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Combine copy, typography and layout defects into one instruction",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/revision-director-demo"))
    return parser.parse_args()


def _finding(route: RevisionRoute) -> RevisionFinding:
    examples = {
        RevisionRoute.COPY: ("The headline message is unclear.", "Rewrite only the headline."),
        RevisionRoute.TYPOGRAPHY: (
            "The headline is too weak in the hierarchy.",
            "Increase headline size 18%, reduce width 10%, and move it 20px upward.",
        ),
        RevisionRoute.LAYOUT: (
            "The CTA competes with the focal point.",
            "Move the CTA into the secondary grid region.",
        ),
        RevisionRoute.COLOR: (
            "CTA contrast is below the approved threshold.",
            "Change only the CTA token to the approved high-contrast color.",
        ),
        RevisionRoute.SCENE: (
            "The background contains unwanted generated text.",
            "Regenerate only the background plate without readable text.",
        ),
        RevisionRoute.PRODUCT: (
            "The protected product edge is damaged.",
            "Re-run masking and edge cleanup from the approved original.",
        ),
        RevisionRoute.STRATEGY: (
            "The value proposition does not support the objective.",
            "Revise only the value proposition from verified product evidence.",
        ),
        RevisionRoute.CONCEPT: (
            "The concept lacks differentiation.",
            "Develop a new visual hook while preserving strategy and assets.",
        ),
    }
    why, action = examples[route]
    return RevisionFinding(
        route=route,
        why=why,
        action=action,
        location="demo primary region",
        source=f"manual_demo:{route.value}",
    )


def main() -> int:
    args = _arguments()
    if args.combined and args.route != "all":
        print("INPUT ERROR: --combined cannot be used with a specific --route", file=sys.stderr)
        return 2
    groups = (
        [
            [
                _finding(RevisionRoute.COPY),
                _finding(RevisionRoute.TYPOGRAPHY),
                _finding(RevisionRoute.LAYOUT),
            ]
        ]
        if args.combined
        else [
            [_finding(route)]
            for route in (
                list(RevisionRoute) if args.route == "all" else [RevisionRoute(args.route)]
            )
        ]
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    director = RevisionDirector()
    history: list[dict] = []
    for findings in groups:
        instruction = director.plan(
            findings,
            requested_by=SupervisorStage.QUALITY_SCORING,
            history=history,
            render_reference="demo-render",
        )
        history = director.append(history, instruction)
        output = args.output_dir / f"{instruction.iteration:02d}-{instruction.route.value}.json"
        output.write_text(instruction.model_dump_json(indent=2), encoding="utf-8")
        print(f"ROUTE:  {instruction.route.value} -> {instruction.target_stage.value}")
        print(f"OWNER:  {instruction.responsible_component}")
        print("KEEP:   " + ", ".join(item.value for item in instruction.keep))
        print("CHANGE: " + ", ".join(item.value for item in instruction.change))
        print("WHY:    " + " | ".join(instruction.why))
        print("ACTION: " + " | ".join(instruction.action))
        print(f"REPORT: {output.resolve()}\n")
        history[-1]["status"] = "completed"
    history_path = args.output_dir / "revision-history.json"
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"HISTORY: {history_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
