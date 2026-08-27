from typing import Any

from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.tools.composition import PostDraft
from app.modules.posts.tools.quality import (
    ApprovalDecision,
    QualityScoringEngine,
    QualityScoringInput,
    QualityThresholds,
)


class QualityScoringStageHandler:
    def __init__(self, *, thresholds: QualityThresholds | None = None) -> None:
        self._thresholds = thresholds or QualityThresholds()
        self._engine = QualityScoringEngine()

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        state = context.workflow_state
        draft = PostDraft.model_validate(_object(state, PostWorkflowSection.POST_DRAFT))
        report = self._engine.score(
            QualityScoringInput(
                marketing_report=_object(state, PostWorkflowSection.QUALITY),
                design_report=_object(state, PostWorkflowSection.DESIGN_QUALITY),
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
    target = {
        ApprovalDecision.MUTATE: SupervisorStage.COPYWRITING,
        ApprovalDecision.RECOMPOSE: SupervisorStage.DESIGN_SPEC,
        ApprovalDecision.REGENERATE: SupervisorStage.CREATIVE_CONCEPT,
    }[report.decision]
    entry = {
        "status": "pending",
        "target_stage": target.value,
        "requested_by": SupervisorStage.QUALITY_SCORING.value,
        "reason": report.reason,
        "recommended_action": report.recommended_action,
        "keep": [
            PostWorkflowSection.SEMANTIC_CONTRACT.value,
            PostWorkflowSection.BRAND.value,
            PostWorkflowSection.PRODUCT.value,
            PostWorkflowSection.ASSETS.value,
        ],
        "change": [item.value for item in report.failed_dimensions],
        "render_checksum": report.render_checksum,
    }
    if history and history[-1] == entry:
        return history
    return [*history, entry]


__all__ = ["QualityScoringStageHandler"]
