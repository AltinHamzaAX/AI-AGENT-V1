import pytest
from pydantic import ValidationError

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import PostSupervisor, SupervisorStage
from app.modules.posts.tools.revision import (
    RevisionDirector,
    RevisionFinding,
    RevisionInstruction,
    RevisionRoute,
)


@pytest.mark.parametrize(
    ("route", "stage", "component", "changed"),
    [
        (RevisionRoute.COPY, SupervisorStage.COPYWRITING, "copywriter", "copy"),
        (
            RevisionRoute.TYPOGRAPHY,
            SupervisorStage.DESIGN_SPEC,
            "typography_engine",
            "design_spec",
        ),
        (
            RevisionRoute.LAYOUT,
            SupervisorStage.DESIGN_SPEC,
            "art_director/layout_engine",
            "design_spec",
        ),
        (RevisionRoute.COLOR, SupervisorStage.DESIGN_SPEC, "color_engine", "design_spec"),
        (
            RevisionRoute.SCENE,
            SupervisorStage.PRODUCTION,
            "scene_generator",
            "generation_artifacts",
        ),
        (
            RevisionRoute.PRODUCT,
            SupervisorStage.ASSET_INTELLIGENCE,
            "asset_pipeline",
            "assets",
        ),
        (
            RevisionRoute.STRATEGY,
            SupervisorStage.MARKETING_STRATEGY,
            "marketing_strategist",
            "marketing_strategy",
        ),
        (
            RevisionRoute.CONCEPT,
            SupervisorStage.CREATIVE_CONCEPT,
            "creative_director",
            "creative_concept",
        ),
    ],
)
def test_each_defect_routes_to_its_smallest_owner(route, stage, component, changed) -> None:
    instruction = RevisionDirector().plan(
        [_finding(route)], requested_by=SupervisorStage.QUALITY_SCORING
    )

    assert instruction.target_stage is stage
    assert instruction.responsible_component == component
    assert changed in {item.value for item in instruction.change}
    assert instruction.why == ["The local relationship failed review."]
    assert instruction.action == ["Correct only the named relationship."]


def test_copy_revision_keeps_strategy_concept_assets_and_product() -> None:
    instruction = RevisionDirector().plan(
        [_finding(RevisionRoute.COPY)], requested_by=SupervisorStage.QUALITY_REVIEW
    )
    kept = set(instruction.keep)
    assert PostWorkflowSection.MARKETING_STRATEGY in kept
    assert PostWorkflowSection.CREATIVE_CONCEPT in kept
    assert PostWorkflowSection.ASSETS in kept
    assert PostWorkflowSection.PRODUCT in kept
    assert PostWorkflowSection.COPY not in kept


def test_product_revision_changes_derived_assets_but_keeps_product_truth() -> None:
    instruction = RevisionDirector().plan(
        [_finding(RevisionRoute.PRODUCT)], requested_by=SupervisorStage.QUALITY_SCORING
    )
    assert instruction.change == [PostWorkflowSection.ASSETS]
    assert PostWorkflowSection.PRODUCT in instruction.keep
    assert PostWorkflowSection.SEMANTIC_CONTRACT in instruction.keep


def test_multiple_findings_route_to_the_earliest_responsible_stage() -> None:
    instruction = RevisionDirector().plan(
        [_finding(RevisionRoute.COLOR), _finding(RevisionRoute.STRATEGY)],
        requested_by=SupervisorStage.QUALITY_SCORING,
    )
    assert instruction.route is RevisionRoute.STRATEGY
    assert instruction.target_stage is SupervisorStage.MARKETING_STRATEGY
    assert set(instruction.change) == {
        PostWorkflowSection.MARKETING_STRATEGY,
        PostWorkflowSection.DESIGN_SPEC,
    }


def test_same_pending_instruction_is_idempotent() -> None:
    director = RevisionDirector()
    first = director.plan(
        [_finding(RevisionRoute.TYPOGRAPHY)], requested_by=SupervisorStage.DESIGN_REVIEW
    )
    history = director.append([], first)
    duplicate = director.plan(
        [_finding(RevisionRoute.TYPOGRAPHY)],
        requested_by=SupervisorStage.DESIGN_REVIEW,
        history=history,
    )
    assert director.append(history, duplicate) == history


def test_new_instruction_increments_iteration_and_has_unique_id() -> None:
    director = RevisionDirector()
    first = director.plan(
        [_finding(RevisionRoute.COPY)], requested_by=SupervisorStage.QUALITY_REVIEW
    )
    history = director.append([], first)
    history[0]["status"] = "completed"
    second = director.plan(
        [_finding(RevisionRoute.COPY)],
        requested_by=SupervisorStage.QUALITY_REVIEW,
        history=history,
    )
    assert first.revision_id != second.revision_id
    assert second.iteration == 2
    assert len(director.append(history, second)) == 2


def test_supervisor_completes_the_exact_pending_instruction() -> None:
    instruction = RevisionDirector().plan(
        [_finding(RevisionRoute.COPY)], requested_by=SupervisorStage.QUALITY_REVIEW
    )
    state = empty_workflow_state()
    state[PostWorkflowSection.REVISION_HISTORY.value] = [instruction.model_dump(mode="json")]

    completed = PostSupervisor().mark_stage_completed(state, SupervisorStage.COPYWRITING)

    assert completed[PostWorkflowSection.REVISION_HISTORY.value][-1]["status"] == "completed"


def test_keep_and_change_overlap_is_rejected() -> None:
    valid = (
        RevisionDirector()
        .plan([_finding(RevisionRoute.COPY)], requested_by=SupervisorStage.QUALITY_REVIEW)
        .model_dump(mode="json")
    )
    valid["keep"].append("copy")
    with pytest.raises(ValidationError, match="keep and change"):
        RevisionInstruction.model_validate(valid)


def test_empty_findings_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one finding"):
        RevisionDirector().plan([], requested_by=SupervisorStage.QUALITY_SCORING)


def _finding(route: RevisionRoute) -> RevisionFinding:
    return RevisionFinding(
        route=route,
        why="The local relationship failed review.",
        action="Correct only the named relationship.",
        location="primary region",
        source=f"test:{route.value}",
    )
