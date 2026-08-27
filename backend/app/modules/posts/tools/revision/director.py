import hashlib
from collections.abc import Sequence
from typing import Any

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage

from .schemas import RevisionFinding, RevisionInstruction, RevisionRoute, RevisionStatus

_ROUTING = {
    RevisionRoute.COPY: (SupervisorStage.COPYWRITING, "copywriter"),
    RevisionRoute.TYPOGRAPHY: (SupervisorStage.DESIGN_SPEC, "typography_engine"),
    RevisionRoute.LAYOUT: (SupervisorStage.DESIGN_SPEC, "art_director/layout_engine"),
    RevisionRoute.COLOR: (SupervisorStage.DESIGN_SPEC, "color_engine"),
    RevisionRoute.SCENE: (SupervisorStage.PRODUCTION, "scene_generator"),
    RevisionRoute.PRODUCT: (SupervisorStage.ASSET_INTELLIGENCE, "asset_pipeline"),
    RevisionRoute.STRATEGY: (SupervisorStage.MARKETING_STRATEGY, "marketing_strategist"),
    RevisionRoute.CONCEPT: (SupervisorStage.CREATIVE_CONCEPT, "creative_director"),
}

_CHANGE = {
    RevisionRoute.COPY: {PostWorkflowSection.COPY},
    RevisionRoute.TYPOGRAPHY: {PostWorkflowSection.DESIGN_SPEC},
    RevisionRoute.LAYOUT: {PostWorkflowSection.DESIGN_SPEC},
    RevisionRoute.COLOR: {PostWorkflowSection.DESIGN_SPEC},
    RevisionRoute.SCENE: {PostWorkflowSection.GENERATION_ARTIFACTS},
    RevisionRoute.PRODUCT: {PostWorkflowSection.ASSETS},
    RevisionRoute.STRATEGY: {PostWorkflowSection.MARKETING_STRATEGY},
    RevisionRoute.CONCEPT: {PostWorkflowSection.CREATIVE_CONCEPT},
}

_PRESERVABLE = {
    PostWorkflowSection.CONVERSATION_CONTEXT,
    PostWorkflowSection.BRIEF,
    PostWorkflowSection.SEMANTIC_CONTRACT,
    PostWorkflowSection.BRAND,
    PostWorkflowSection.PRODUCT,
    PostWorkflowSection.AUDIENCE,
    PostWorkflowSection.RESEARCH,
    PostWorkflowSection.MARKETING_STRATEGY,
    PostWorkflowSection.CREATIVE_CONCEPT,
    PostWorkflowSection.COPY,
    PostWorkflowSection.ART_DIRECTION,
    PostWorkflowSection.DESIGN_SPEC,
    PostWorkflowSection.ASSETS,
    PostWorkflowSection.GENERATION_PLAN,
}


class RevisionDirector:
    """Translate quality findings into one minimal deterministic revision plan."""

    def plan(
        self,
        findings: Sequence[RevisionFinding],
        *,
        requested_by: SupervisorStage,
        history: Sequence[Any] = (),
        render_reference: str | None = None,
    ) -> RevisionInstruction:
        if not findings:
            raise ValueError("revision planning requires at least one finding")
        ordered = sorted(
            findings,
            key=lambda item: (
                self._stage_index(item.route),
                item.route.value,
                item.source,
                item.why,
                item.action,
            ),
        )
        primary = ordered[0]
        target_stage, component = _ROUTING[primary.route]
        change = set().union(*(_CHANGE[item.route] for item in ordered))
        invalidated = {
            section
            for stage in DEFAULT_SUPERVISOR_PLAN.downstream(target_stage)
            for section in DEFAULT_SUPERVISOR_PLAN.get(stage).output_sections
        }
        keep = _PRESERVABLE - invalidated - change
        iteration = self._next_iteration(history)
        instruction_id = self._id(
            requested_by=requested_by,
            route=primary.route,
            findings=ordered,
            render_reference=render_reference,
            iteration=iteration,
        )
        return RevisionInstruction(
            revision_id=instruction_id,
            iteration=iteration,
            status=RevisionStatus.PENDING,
            route=primary.route,
            target_stage=target_stage,
            requested_by=requested_by,
            responsible_component=component,
            keep=sorted(keep, key=lambda item: item.value),
            change=sorted(change, key=lambda item: item.value),
            why=list(dict.fromkeys(item.why for item in ordered)),
            action=list(dict.fromkeys(item.action for item in ordered)),
            findings=ordered,
            render_reference=render_reference,
        )

    def append(
        self,
        history: Sequence[Any],
        instruction: RevisionInstruction,
    ) -> list[Any]:
        existing = list(history)
        if existing and isinstance(existing[-1], dict):
            latest = existing[-1]
            if latest.get("status") == RevisionStatus.PENDING.value:
                try:
                    previous = RevisionInstruction.model_validate(latest)
                except ValueError:
                    previous = None
                if previous is not None and previous.signature() == instruction.signature():
                    return existing
        return [*existing, instruction.model_dump(mode="json")]

    @staticmethod
    def _next_iteration(history: Sequence[Any]) -> int:
        iterations = [
            item.get("iteration")
            for item in history
            if isinstance(item, dict)
            and isinstance(item.get("iteration"), int)
            and not isinstance(item.get("iteration"), bool)
        ]
        return max([len(history), *iterations], default=0) + 1

    @staticmethod
    def _stage_index(route: RevisionRoute) -> int:
        target = _ROUTING[route][0]
        return next(
            index
            for index, policy in enumerate(DEFAULT_SUPERVISOR_PLAN.stages)
            if policy.stage is target
        )

    @staticmethod
    def _id(
        *,
        requested_by: SupervisorStage,
        route: RevisionRoute,
        findings: Sequence[RevisionFinding],
        render_reference: str | None,
        iteration: int,
    ) -> str:
        source = "|".join(
            [
                requested_by.value,
                route.value,
                render_reference or "none",
                str(iteration),
                *(
                    f"{item.route.value}:{item.source}:{item.why}:{item.action}"
                    for item in findings
                ),
            ]
        )
        return "rev_" + hashlib.sha256(source.encode()).hexdigest()[:16]


__all__ = ["RevisionDirector"]
