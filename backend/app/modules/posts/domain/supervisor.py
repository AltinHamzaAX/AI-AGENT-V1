from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.modules.posts.domain.clarification import ClarificationPlan
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.state import validate_workflow_state


class SupervisorAction(StrEnum):
    CONTINUE = "CONTINUE"
    SKIP = "SKIP"
    RETRY = "RETRY"
    REVISE = "REVISE"
    STOP = "STOP"


class SupervisorStage(StrEnum):
    CLIENT_UNDERSTANDING = "client_understanding"
    SEMANTIC_CONTRACT = "semantic_contract"
    ASSET_INTELLIGENCE = "asset_intelligence"
    BRAND_PRODUCT = "brand_product"
    AUDIENCE_INTELLIGENCE = "audience_intelligence"
    EXTERNAL_RESEARCH = "external_research"
    MARKETING_STRATEGY = "marketing_strategy"
    CREATIVE_CONCEPT = "creative_concept"
    COPYWRITING = "copywriting"
    ART_DIRECTION = "art_direction"
    DESIGN_SPEC = "design_spec"
    GENERATION_PLANNING = "generation_planning"
    PRODUCTION = "production"
    SCENE_PURITY = "scene_purity"
    COMPOSITION = "composition"
    VERIFICATION = "verification"
    QUALITY_REVIEW = "quality_review"
    DESIGN_REVIEW = "design_review"
    QUALITY_SCORING = "quality_scoring"


@dataclass(frozen=True, slots=True)
class SupervisorStagePolicy:
    stage: SupervisorStage
    dependencies: tuple[SupervisorStage, ...] = ()
    required_sections: tuple[PostWorkflowSection, ...] = ()
    output_sections: tuple[PostWorkflowSection, ...] = ()
    optional: bool = False
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.stage in self.dependencies:
            raise ValueError("A supervisor stage cannot depend on itself")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("Supervisor stage max_attempts must be between 1 and 10")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Supervisor stage dependencies must be unique")
        if len(set(self.required_sections)) != len(self.required_sections):
            raise ValueError("Supervisor required sections must be unique")
        if len(set(self.output_sections)) != len(self.output_sections):
            raise ValueError("Supervisor output sections must be unique")
        if PostWorkflowSection.SUPERVISOR in self.output_sections:
            raise ValueError("Specialist stages cannot write supervisor progress")


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    action: SupervisorAction
    next_stage: SupervisorStage | None
    reason: str
    required_inputs: tuple[str, ...] = ()
    state_requirements: tuple[PostWorkflowSection, ...] = ()
    terminal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "next_stage": self.next_stage.value if self.next_stage else None,
            "reason": self.reason,
            "required_inputs": list(self.required_inputs),
            "state_requirements": [section.value for section in self.state_requirements],
            "terminal": self.terminal,
        }


class SupervisorPlan:
    def __init__(self, stages: tuple[SupervisorStagePolicy, ...]) -> None:
        if not stages:
            raise ValueError("Supervisor plan must contain at least one stage")
        self._stages = stages
        self._by_name = {policy.stage: policy for policy in stages}
        if len(self._by_name) != len(stages):
            raise ValueError("Supervisor plan stages must be unique")
        for policy in stages:
            missing = set(policy.dependencies) - set(self._by_name)
            if missing:
                names = ", ".join(sorted(stage.value for stage in missing))
                raise ValueError(f"Supervisor stage has unknown dependencies: {names}")
        self._validate_acyclic()

    @property
    def stages(self) -> tuple[SupervisorStagePolicy, ...]:
        return self._stages

    def get(self, stage: SupervisorStage) -> SupervisorStagePolicy:
        return self._by_name[stage]

    def downstream(self, stage: SupervisorStage) -> tuple[SupervisorStage, ...]:
        affected = {stage}
        changed = True
        while changed:
            changed = False
            for policy in self._stages:
                if policy.stage not in affected and affected.intersection(policy.dependencies):
                    affected.add(policy.stage)
                    changed = True
        return tuple(policy.stage for policy in self._stages if policy.stage in affected)

    def _validate_acyclic(self) -> None:
        visiting: set[SupervisorStage] = set()
        visited: set[SupervisorStage] = set()

        def visit(stage: SupervisorStage) -> None:
            if stage in visiting:
                raise ValueError("Supervisor plan dependencies must be acyclic")
            if stage in visited:
                return
            visiting.add(stage)
            for dependency in self._by_name[stage].dependencies:
                visit(dependency)
            visiting.remove(stage)
            visited.add(stage)

        for stage in self._by_name:
            visit(stage)


DEFAULT_SUPERVISOR_PLAN = SupervisorPlan(
    (
        SupervisorStagePolicy(
            SupervisorStage.CLIENT_UNDERSTANDING,
            required_sections=(PostWorkflowSection.CONVERSATION_CONTEXT,),
            output_sections=(PostWorkflowSection.BRIEF,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.SEMANTIC_CONTRACT,
            dependencies=(SupervisorStage.CLIENT_UNDERSTANDING,),
            required_sections=(PostWorkflowSection.BRIEF,),
            output_sections=(PostWorkflowSection.SEMANTIC_CONTRACT,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.ASSET_INTELLIGENCE,
            dependencies=(SupervisorStage.SEMANTIC_CONTRACT,),
            required_sections=(PostWorkflowSection.SEMANTIC_CONTRACT,),
            output_sections=(PostWorkflowSection.ASSETS,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.BRAND_PRODUCT,
            dependencies=(SupervisorStage.SEMANTIC_CONTRACT,),
            required_sections=(PostWorkflowSection.SEMANTIC_CONTRACT,),
            output_sections=(PostWorkflowSection.BRAND, PostWorkflowSection.PRODUCT),
        ),
        SupervisorStagePolicy(
            SupervisorStage.AUDIENCE_INTELLIGENCE,
            dependencies=(SupervisorStage.BRAND_PRODUCT,),
            required_sections=(PostWorkflowSection.SEMANTIC_CONTRACT,),
            output_sections=(PostWorkflowSection.AUDIENCE,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.EXTERNAL_RESEARCH,
            dependencies=(SupervisorStage.AUDIENCE_INTELLIGENCE,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.AUDIENCE,
            ),
            output_sections=(PostWorkflowSection.RESEARCH,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.MARKETING_STRATEGY,
            dependencies=(SupervisorStage.EXTERNAL_RESEARCH,),
            required_sections=(
                PostWorkflowSection.BRIEF,
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.BRAND,
                PostWorkflowSection.PRODUCT,
                PostWorkflowSection.AUDIENCE,
                PostWorkflowSection.RESEARCH,
            ),
            output_sections=(PostWorkflowSection.MARKETING_STRATEGY,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.CREATIVE_CONCEPT,
            dependencies=(SupervisorStage.MARKETING_STRATEGY,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.BRAND,
                PostWorkflowSection.AUDIENCE,
                PostWorkflowSection.RESEARCH,
                PostWorkflowSection.MARKETING_STRATEGY,
            ),
            output_sections=(PostWorkflowSection.CREATIVE_CONCEPT,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.COPYWRITING,
            dependencies=(SupervisorStage.CREATIVE_CONCEPT,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.CREATIVE_CONCEPT,
                PostWorkflowSection.MARKETING_STRATEGY,
                PostWorkflowSection.BRAND,
            ),
            output_sections=(PostWorkflowSection.COPY,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.ART_DIRECTION,
            dependencies=(
                SupervisorStage.COPYWRITING,
                SupervisorStage.ASSET_INTELLIGENCE,
            ),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.CREATIVE_CONCEPT,
                PostWorkflowSection.COPY,
                PostWorkflowSection.BRAND,
                PostWorkflowSection.ASSETS,
            ),
            output_sections=(PostWorkflowSection.ART_DIRECTION,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.DESIGN_SPEC,
            dependencies=(SupervisorStage.COPYWRITING, SupervisorStage.ART_DIRECTION),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.COPY,
                PostWorkflowSection.ART_DIRECTION,
            ),
            output_sections=(PostWorkflowSection.DESIGN_SPEC,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.GENERATION_PLANNING,
            dependencies=(SupervisorStage.DESIGN_SPEC, SupervisorStage.ASSET_INTELLIGENCE),
            required_sections=(
                PostWorkflowSection.DESIGN_SPEC,
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.ASSETS,
            ),
            output_sections=(PostWorkflowSection.GENERATION_PLAN,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.PRODUCTION,
            dependencies=(SupervisorStage.GENERATION_PLANNING,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.CREATIVE_CONCEPT,
                PostWorkflowSection.ART_DIRECTION,
                PostWorkflowSection.DESIGN_SPEC,
                PostWorkflowSection.ASSETS,
                PostWorkflowSection.GENERATION_PLAN,
            ),
            output_sections=(PostWorkflowSection.GENERATION_ARTIFACTS,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.SCENE_PURITY,
            dependencies=(SupervisorStage.PRODUCTION,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.GENERATION_PLAN,
                PostWorkflowSection.GENERATION_ARTIFACTS,
            ),
            # Writes revision history so a contaminated plate can send production
            # back for another scene instead of reaching the composer.
            output_sections=(
                PostWorkflowSection.SCENE_PURITY,
                PostWorkflowSection.REVISION_HISTORY,
            ),
        ),
        SupervisorStagePolicy(
            SupervisorStage.COMPOSITION,
            dependencies=(SupervisorStage.SCENE_PURITY,),
            required_sections=(
                PostWorkflowSection.COPY,
                PostWorkflowSection.DESIGN_SPEC,
                PostWorkflowSection.ASSETS,
                PostWorkflowSection.GENERATION_ARTIFACTS,
                PostWorkflowSection.SCENE_PURITY,
            ),
            output_sections=(PostWorkflowSection.POST_DRAFT,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.VERIFICATION,
            dependencies=(SupervisorStage.COMPOSITION,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.COPY,
                PostWorkflowSection.DESIGN_SPEC,
                PostWorkflowSection.POST_DRAFT,
            ),
            # Hard gates run before anything scores the post, so a blocked render
            # never costs a marketing or design review, and no score can be
            # pointed at afterwards as a reason to let it through.
            output_sections=(PostWorkflowSection.VERIFICATION,),
        ),
        SupervisorStagePolicy(
            SupervisorStage.QUALITY_REVIEW,
            dependencies=(SupervisorStage.VERIFICATION,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.MARKETING_STRATEGY,
                PostWorkflowSection.COPY,
                PostWorkflowSection.POST_DRAFT,
            ),
            output_sections=(
                PostWorkflowSection.QUALITY,
                PostWorkflowSection.REVISION_HISTORY,
            ),
        ),
        SupervisorStagePolicy(
            SupervisorStage.DESIGN_REVIEW,
            dependencies=(SupervisorStage.QUALITY_REVIEW,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.ART_DIRECTION,
                PostWorkflowSection.DESIGN_SPEC,
                PostWorkflowSection.POST_DRAFT,
                PostWorkflowSection.QUALITY,
            ),
            output_sections=(
                PostWorkflowSection.DESIGN_QUALITY,
                PostWorkflowSection.REVISION_HISTORY,
            ),
        ),
        SupervisorStagePolicy(
            SupervisorStage.QUALITY_SCORING,
            dependencies=(SupervisorStage.DESIGN_REVIEW,),
            required_sections=(
                PostWorkflowSection.SEMANTIC_CONTRACT,
                PostWorkflowSection.CREATIVE_CONCEPT,
                PostWorkflowSection.POST_DRAFT,
                PostWorkflowSection.VERIFICATION,
                PostWorkflowSection.QUALITY,
                PostWorkflowSection.DESIGN_QUALITY,
            ),
            output_sections=(
                PostWorkflowSection.QUALITY_APPROVAL,
                PostWorkflowSection.REVISION_HISTORY,
            ),
        ),
    )
)


class PostSupervisor:
    """Deterministic Posts control-plane over persisted workflow state."""

    def __init__(self, plan: SupervisorPlan = DEFAULT_SUPERVISOR_PLAN) -> None:
        self._plan = plan

    def policy(self, stage: SupervisorStage) -> SupervisorStagePolicy:
        return self._plan.get(stage)

    def decide(
        self,
        workflow_state: dict[str, Any],
        *,
        available_stages: frozenset[SupervisorStage] | None = None,
    ) -> SupervisorDecision:
        state = validate_workflow_state(workflow_state)
        progress = _progress(state)
        quality = state[PostWorkflowSection.QUALITY.value]
        design_quality = state[PostWorkflowSection.DESIGN_QUALITY.value]
        verification = state[PostWorkflowSection.VERIFICATION.value]
        approval = state[PostWorkflowSection.QUALITY_APPROVAL.value]
        # Read before any score, and terminal: a hard verification gate is not
        # weighed against how good the post looks, so nothing below can revive a
        # render that failed one.
        if verification.get("decision") == "BLOCKED":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=None,
                reason="hard verification gate blocked the workflow",
                state_requirements=(PostWorkflowSection.VERIFICATION,),
                terminal=True,
            )
        if quality.get("hard_fail") is True or quality.get("decision") in {
            "BLOCKED",
            "REJECT",
        }:
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=None,
                reason="quality hard gate blocked the workflow",
                state_requirements=(PostWorkflowSection.QUALITY,),
                terminal=True,
            )
        if approval.get("decision") == "REJECT":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=None,
                reason="quality approval engine rejected the render",
                state_requirements=(PostWorkflowSection.QUALITY_APPROVAL,),
                terminal=True,
            )

        revision_target = _pending_revision_target(state)
        if revision_target is not None:
            if revision_target is SupervisorStage.SEMANTIC_CONTRACT:
                return SupervisorDecision(
                    action=SupervisorAction.STOP,
                    next_stage=revision_target,
                    reason="semantic contract is immutable and cannot be revised",
                    state_requirements=(PostWorkflowSection.SEMANTIC_CONTRACT,),
                    terminal=True,
                )
            policy = self._plan.get(revision_target)
            missing = _missing_sections(state, policy.required_sections)
            if missing:
                return _missing_input_decision(policy, missing)
            unavailable = _unavailable(revision_target, available_stages)
            if unavailable:
                return unavailable
            return SupervisorDecision(
                action=SupervisorAction.REVISE,
                next_stage=revision_target,
                reason="targeted revision requested by quality review",
                state_requirements=policy.required_sections,
            )

        completed = set(progress["completed_stages"])
        skipped = set(progress["skipped_stages"])
        invalidated = set(progress["invalidated_stages"])
        requested_skips = set(progress["requested_skips"])
        attempts = progress["stage_attempts"]
        clarification = _clarification_decision(state, completed | skipped)
        if clarification is not None:
            return clarification
        for policy in self._plan.stages:
            name = policy.stage.value
            if name in completed or name in skipped:
                continue
            if name not in invalidated and _outputs_present(state, policy.output_sections):
                return SupervisorDecision(
                    action=SupervisorAction.SKIP,
                    next_stage=policy.stage,
                    reason="stage output already exists in persisted workflow state",
                    state_requirements=policy.required_sections,
                )
            if policy.optional and name in requested_skips:
                return SupervisorDecision(
                    action=SupervisorAction.SKIP,
                    next_stage=policy.stage,
                    reason="optional stage explicitly skipped",
                    state_requirements=policy.required_sections,
                )
            unresolved = [
                dependency.value
                for dependency in policy.dependencies
                if dependency.value not in completed and dependency.value not in skipped
            ]
            if unresolved:
                continue
            missing = _missing_sections(state, policy.required_sections)
            if missing:
                return _missing_input_decision(policy, missing)
            stage_attempts = attempts.get(name, 0)
            if stage_attempts >= policy.max_attempts:
                return SupervisorDecision(
                    action=SupervisorAction.STOP,
                    next_stage=policy.stage,
                    reason="stage retry limit exhausted",
                    state_requirements=policy.required_sections,
                    terminal=True,
                )
            unavailable = _unavailable(policy.stage, available_stages)
            if unavailable:
                return unavailable
            return SupervisorDecision(
                action=(SupervisorAction.RETRY if stage_attempts else SupervisorAction.CONTINUE),
                next_stage=policy.stage,
                reason=(
                    "retrying incomplete stage after a recoverable failure"
                    if stage_attempts
                    else "stage dependencies and required inputs are satisfied"
                ),
                state_requirements=policy.required_sections,
            )

        if len(completed | skipped) < len(self._plan.stages):
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=None,
                reason="workflow dependency graph cannot make progress",
                terminal=True,
            )
        verification_enabled = any(
            policy.stage is SupervisorStage.VERIFICATION for policy in self._plan.stages
        )
        if verification_enabled and verification.get("decision") != "PASS":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=SupervisorStage.VERIFICATION,
                reason="every hard verification gate must pass before completion",
                state_requirements=(PostWorkflowSection.VERIFICATION,),
            )
        if quality.get("decision") != "PASS":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=SupervisorStage.QUALITY_REVIEW,
                reason="explicit quality approval is required before completion",
                state_requirements=(PostWorkflowSection.QUALITY,),
            )
        design_review_enabled = any(
            policy.stage is SupervisorStage.DESIGN_REVIEW for policy in self._plan.stages
        )
        if design_review_enabled and design_quality.get("decision") != "PASS":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=SupervisorStage.DESIGN_REVIEW,
                reason="explicit senior design approval is required before completion",
                state_requirements=(PostWorkflowSection.DESIGN_QUALITY,),
            )
        scoring_enabled = any(
            policy.stage is SupervisorStage.QUALITY_SCORING for policy in self._plan.stages
        )
        if scoring_enabled and approval.get("decision") != "PASS":
            return SupervisorDecision(
                action=SupervisorAction.STOP,
                next_stage=SupervisorStage.QUALITY_SCORING,
                reason="standardized quality thresholds require explicit approval",
                state_requirements=(PostWorkflowSection.QUALITY_APPROVAL,),
            )
        return SupervisorDecision(
            action=SupervisorAction.STOP,
            next_stage=None,
            reason="workflow complete and quality approved",
            state_requirements=(
                PostWorkflowSection.VERIFICATION,
                PostWorkflowSection.QUALITY,
                PostWorkflowSection.DESIGN_QUALITY,
                PostWorkflowSection.QUALITY_APPROVAL,
            ),
            terminal=True,
        )

    def record_decision(
        self,
        workflow_state: dict[str, Any],
        decision: SupervisorDecision,
    ) -> dict[str, Any]:
        state = validate_workflow_state(workflow_state)
        progress = _progress(state)
        progress["last_decision"] = decision.to_dict()
        if decision.next_stage is not None:
            stage_name = decision.next_stage.value
            if decision.action is SupervisorAction.SKIP:
                progress["skipped_stages"] = _append_unique(progress["skipped_stages"], stage_name)
                progress["current_stage"] = None
            elif decision.action in {
                SupervisorAction.CONTINUE,
                SupervisorAction.RETRY,
                SupervisorAction.REVISE,
            }:
                if decision.action is SupervisorAction.REVISE:
                    affected = {stage.value for stage in self._plan.downstream(decision.next_stage)}
                    progress["invalidated_stages"] = [
                        stage.value for stage in self._plan.downstream(decision.next_stage)
                    ]
                    progress["completed_stages"] = [
                        stage for stage in progress["completed_stages"] if stage not in affected
                    ]
                    progress["skipped_stages"] = [
                        stage for stage in progress["skipped_stages"] if stage not in affected
                    ]
                progress["current_stage"] = stage_name
                progress["stage_attempts"][stage_name] = (
                    progress["stage_attempts"].get(stage_name, 0) + 1
                )
        state[PostWorkflowSection.SUPERVISOR.value] = progress
        return state

    def mark_stage_completed(
        self,
        workflow_state: dict[str, Any],
        stage: SupervisorStage,
    ) -> dict[str, Any]:
        state = validate_workflow_state(workflow_state)
        progress = _progress(state)
        progress["completed_stages"] = _append_unique(progress["completed_stages"], stage.value)
        progress["invalidated_stages"] = [
            name for name in progress["invalidated_stages"] if name != stage.value
        ]
        progress["current_stage"] = None
        state[PostWorkflowSection.SUPERVISOR.value] = progress
        history = state[PostWorkflowSection.REVISION_HISTORY.value]
        if history:
            latest = history[-1]
            if (
                isinstance(latest, dict)
                and latest.get("status") == "pending"
                and latest.get("target_stage") == stage.value
            ):
                latest["status"] = "completed"
        return state


def _progress(state: dict[str, Any]) -> dict[str, Any]:
    raw = state[PostWorkflowSection.SUPERVISOR.value]
    expected = {
        "current_stage",
        "completed_stages",
        "skipped_stages",
        "invalidated_stages",
        "requested_skips",
        "stage_attempts",
        "last_decision",
    }
    if not raw:
        return {
            "current_stage": None,
            "completed_stages": [],
            "skipped_stages": [],
            "invalidated_stages": [],
            "requested_skips": [],
            "stage_attempts": {},
            "last_decision": {},
        }
    if set(raw) != expected:
        raise ValueError("Persisted supervisor progress has an invalid shape")
    current = raw["current_stage"]
    if current is not None:
        SupervisorStage(current)
    lists: dict[str, list[str]] = {}
    for key in (
        "completed_stages",
        "skipped_stages",
        "invalidated_stages",
        "requested_skips",
    ):
        values = raw[key]
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"Supervisor progress '{key}' must be a string array")
        if len(values) != len(set(values)):
            raise ValueError(f"Supervisor progress '{key}' must contain unique stages")
        for value in values:
            SupervisorStage(value)
        lists[key] = list(values)
    attempts = raw["stage_attempts"]
    if not isinstance(attempts, dict):
        raise ValueError("Supervisor stage_attempts must be an object")
    normalized_attempts: dict[str, int] = {}
    for stage, count in attempts.items():
        SupervisorStage(stage)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Supervisor stage attempts must be non-negative integers")
        normalized_attempts[stage] = count
    last_decision = raw["last_decision"]
    if not isinstance(last_decision, dict):
        raise ValueError("Supervisor last_decision must be an object")
    return {
        "current_stage": current,
        **lists,
        "stage_attempts": normalized_attempts,
        "last_decision": dict(last_decision),
    }


def _outputs_present(state: dict[str, Any], sections: tuple[PostWorkflowSection, ...]) -> bool:
    return bool(sections) and all(bool(state[section.value]) for section in sections)


def _missing_sections(
    state: dict[str, Any], sections: tuple[PostWorkflowSection, ...]
) -> tuple[PostWorkflowSection, ...]:
    return tuple(section for section in sections if not state[section.value])


def _missing_input_decision(
    policy: SupervisorStagePolicy,
    missing: tuple[PostWorkflowSection, ...],
) -> SupervisorDecision:
    return SupervisorDecision(
        action=SupervisorAction.STOP,
        next_stage=policy.stage,
        reason="required workflow inputs are missing",
        required_inputs=tuple(section.value for section in missing),
        state_requirements=policy.required_sections,
    )


def _unavailable(
    stage: SupervisorStage,
    available: frozenset[SupervisorStage] | None,
) -> SupervisorDecision | None:
    if available is None or stage in available:
        return None
    return SupervisorDecision(
        action=SupervisorAction.STOP,
        next_stage=stage,
        reason="stage handler is not registered",
        required_inputs=(f"stage_handler:{stage.value}",),
    )


def _pending_revision_target(state: dict[str, Any]) -> SupervisorStage | None:
    history = state[PostWorkflowSection.REVISION_HISTORY.value]
    if not history:
        return None
    latest = history[-1]
    if not isinstance(latest, dict) or latest.get("status") != "pending":
        return None
    target = latest.get("target_stage")
    if not isinstance(target, str):
        raise ValueError("Pending revision must declare target_stage")
    return SupervisorStage(target)


def _clarification_decision(
    state: dict[str, Any],
    completed_or_skipped: set[str],
) -> SupervisorDecision | None:
    if SupervisorStage.CLIENT_UNDERSTANDING.value not in completed_or_skipped:
        return None
    brief = state[PostWorkflowSection.BRIEF.value]
    raw = brief.get("clarification")
    if raw is None:
        return None
    plan = ClarificationPlan.model_validate(raw)
    if not plan.requires_user_input:
        return None
    return SupervisorDecision(
        action=SupervisorAction.STOP,
        next_stage=SupervisorStage.CLIENT_UNDERSTANDING,
        reason="critical client information requires clarification",
        required_inputs=tuple(
            f"clarification:{question.field.value}" for question in plan.questions
        ),
        state_requirements=(PostWorkflowSection.BRIEF,),
    )


def _append_unique(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]


__all__ = [
    "DEFAULT_SUPERVISOR_PLAN",
    "PostSupervisor",
    "SupervisorAction",
    "SupervisorDecision",
    "SupervisorPlan",
    "SupervisorStage",
    "SupervisorStagePolicy",
]
