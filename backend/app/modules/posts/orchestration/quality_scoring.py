from typing import Any

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.services.concept_memory import ConceptMemoryService, PostMemoryScopeResolver
from app.modules.posts.tools.composition import PostDraft
from app.modules.posts.tools.quality import (
    ApprovalDecision,
    QualityScoringEngine,
    QualityScoringInput,
    QualityThresholds,
)
from app.modules.posts.tools.revision import RevisionDirector, RevisionFinding, RevisionRoute


class QualityScoringStageHandler:
    def __init__(
        self,
        *,
        thresholds: QualityThresholds | None = None,
        concept_memory: ConceptMemoryService | None = None,
        memory_scope_resolver: PostMemoryScopeResolver | None = None,
    ) -> None:
        if (concept_memory is None) != (memory_scope_resolver is None):
            raise ValueError("concept_memory and memory_scope_resolver must be configured together")
        self._thresholds = thresholds or QualityThresholds()
        self._engine = QualityScoringEngine()
        self._concept_memory = concept_memory
        self._memory_scope_resolver = memory_scope_resolver

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        state = context.workflow_state
        draft = PostDraft.model_validate(_object(state, PostWorkflowSection.POST_DRAFT))
        report = self._engine.score(
            QualityScoringInput(
                marketing_report=_object(state, PostWorkflowSection.QUALITY),
                design_report=_object(state, PostWorkflowSection.DESIGN_QUALITY),
                vision_report=_object(state, PostWorkflowSection.VISION_QUALITY),
                creative_direction=_object(state, PostWorkflowSection.CREATIVE_CONCEPT),
                verification_report=_object(state, PostWorkflowSection.VERIFICATION),
                render_checksum=draft.final_asset.checksum,
                contract_fingerprint=draft.contract_fingerprint,
                thresholds=self._thresholds,
            )
        )
        history = list(_array(state, PostWorkflowSection.REVISION_HISTORY))
        if report.decision not in {ApprovalDecision.PASS, ApprovalDecision.REJECT}:
            history = _request_revision(history, report)
        if (
            report.decision is ApprovalDecision.PASS
            and self._concept_memory is not None
            and self._memory_scope_resolver is not None
        ):
            scope = await self._memory_scope_resolver.resolve_project_scope(
                post_id=context.post_id
            )
            if scope is None:
                raise ValueError("approved creative memory scope could not resolve the post")
            await self._concept_memory.remember_approved(
                scope=scope,
                direction=CreativeDirection.model_validate(
                    _object(state, PostWorkflowSection.CREATIVE_CONCEPT)
                ),
                copy=CopyDraft.model_validate(_object(state, PostWorkflowSection.COPY)),
                art=ArtDirection.model_validate(
                    _object(state, PostWorkflowSection.ART_DIRECTION)
                ),
                design_spec=DesignSpec.model_validate(
                    _object(state, PostWorkflowSection.DESIGN_SPEC)
                ),
                generation_id=context.generation_id,
            )
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.QUALITY_APPROVAL: report.model_dump(mode="json"),
                PostWorkflowSection.REVISION_HISTORY: history,
            }
        )


def _object(state: dict[str, Any], section: PostWorkflowSection) -> dict[str, Any]:
    value = state.get(section.value)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{section.value} must be a populated object")
    return value


def _array(state: dict[str, Any], section: PostWorkflowSection) -> list[Any]:
    value = state.get(section.value)
    if not isinstance(value, list):
        raise ValueError(f"{section.value} must be an array")
    return value


def _request_revision(history: list[Any], report) -> list[Any]:
    findings = [
        RevisionFinding(
            route=_quality_route(dimension),
            why=f"{dimension.value} scored below its configured threshold.",
            action=report.recommended_action or "Correct only this failed dimension.",
            source=f"quality_scoring:{dimension.value}",
        )
        for dimension in report.failed_dimensions
    ]
    if not findings:
        findings = [
            RevisionFinding(
                route=RevisionRoute.STRATEGY,
                why=report.reason,
                action=report.recommended_action or "Improve the overall quality score.",
                source="quality_scoring:overall",
            )
        ]
    director = RevisionDirector()
    instruction = director.plan(
        findings,
        requested_by=SupervisorStage.QUALITY_SCORING,
        history=history,
        render_reference=report.render_checksum,
    )
    return director.append(history, instruction)


def _quality_route(dimension) -> RevisionRoute:
    from app.modules.posts.tools.quality import QualityDimension

    return {
        QualityDimension.MARKETING_EFFECTIVENESS: RevisionRoute.STRATEGY,
        QualityDimension.CREATIVE_CONCEPT: RevisionRoute.CONCEPT,
        QualityDimension.COMPOSITION: RevisionRoute.LAYOUT,
        QualityDimension.VISUAL_HIERARCHY: RevisionRoute.LAYOUT,
        QualityDimension.TYPOGRAPHY: RevisionRoute.TYPOGRAPHY,
        QualityDimension.COLOR: RevisionRoute.COLOR,
        QualityDimension.BRAND_FIT: RevisionRoute.LAYOUT,
        QualityDimension.PRODUCT_FIDELITY: RevisionRoute.PRODUCT,
        QualityDimension.AUDIENCE_FIT: RevisionRoute.STRATEGY,
        QualityDimension.PLATFORM_FIT: RevisionRoute.LAYOUT,
        QualityDimension.DIFFERENTIATION: RevisionRoute.CONCEPT,
        QualityDimension.OVERALL_POLISH: RevisionRoute.LAYOUT,
    }[dimension]


__all__ = ["QualityScoringStageHandler"]
