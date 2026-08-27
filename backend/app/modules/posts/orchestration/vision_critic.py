from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.agents.vision_critic import (
    VisionCritic,
    VisionCriticDecision,
    VisionCriticInput,
    VisionCriticReport,
    VisionDimension,
)
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import SupervisorStageContext, SupervisorStageResult
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.composition import PostDraft
from app.modules.posts.tools.revision import RevisionDirector, RevisionFinding, RevisionRoute


class VisionCriticStageHandler:
    def __init__(
        self,
        providers: ProviderBundle,
        *,
        max_revisions: int = 2,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        if not 1 <= max_revisions <= 5:
            raise ValueError("max_revisions must be between 1 and 5")
        self._providers = providers
        self._max_revisions = max_revisions
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        state = context.workflow_state
        draft = PostDraft.model_validate(_object(state, PostWorkflowSection.POST_DRAFT))
        history = list(_array(state, PostWorkflowSection.REVISION_HISTORY))
        revision_requests = _revision_requests(history)
        providers = self._providers
        if self._trace_recorder is not None:
            providers = trace_provider_bundle(
                providers,
                recorder=self._trace_recorder,
                invocation=InvocationContext(
                    correlation_id=context.job_id,
                    post_id=context.post_id,
                    generation_id=context.generation_id,
                ),
            )
        image = await providers.storage.get(key=draft.final_asset.storage_key)
        report = await VisionCritic(providers.vision).review(
            VisionCriticInput(
                final_image=image,
                final_mime_type=draft.final_asset.mime_type,
                semantic_contract=_object(state, PostWorkflowSection.SEMANTIC_CONTRACT),
                copy_draft=CopyDraft.model_validate(_object(state, PostWorkflowSection.COPY)),
                design_spec=DesignSpec.model_validate(
                    _object(state, PostWorkflowSection.DESIGN_SPEC)
                ),
                post_draft=draft,
                asset_policies=[
                    AssetPolicy.model_validate(item)
                    for item in _array(state, PostWorkflowSection.ASSETS)
                ],
            ),
            revision_requests=revision_requests,
        )
        if report.decision is VisionCriticDecision.REVISE:
            if revision_requests >= self._max_revisions:
                raise NonRetryableJobError(
                    f"visual perception still fails after {revision_requests} revisions: "
                    f"{report.summary}"
                )
            history = _request_revision(history, report)
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.VISION_QUALITY: report.model_dump(mode="json"),
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


def _revision_requests(history: list[Any]) -> int:
    return sum(
        1 for item in history if isinstance(item, dict)
        and item.get("requested_by") == SupervisorStage.VISION_REVIEW.value
    )


def _request_revision(history: list[Any], report: VisionCriticReport) -> list[Any]:
    findings = [
        RevisionFinding(
            route=_route(issue.dimension),
            why=f"{issue.expected} Expected; observed: {issue.observed}",
            action=issue.recommended_action,
            location=issue.region,
            source=f"vision_critic:{issue.dimension.value}:{issue.confidence:.2f}",
        )
        for issue in report.issues
    ]
    director = RevisionDirector()
    instruction = director.plan(
        findings, requested_by=SupervisorStage.VISION_REVIEW, history=history,
        render_reference=report.render_fingerprint,
    )
    return director.append(history, instruction)


def _route(dimension: VisionDimension) -> RevisionRoute:
    if dimension in {VisionDimension.PRODUCT_FIDELITY, VisionDimension.LOGO_APPEARANCE}:
        return RevisionRoute.PRODUCT
    if dimension in {VisionDimension.AI_ARTIFACTS, VisionDimension.DISTORTION}:
        return RevisionRoute.SCENE
    if dimension in {
        VisionDimension.READABILITY, VisionDimension.CTA_VISIBILITY,
        VisionDimension.TEXT_LEGIBILITY,
    }:
        return RevisionRoute.TYPOGRAPHY
    return RevisionRoute.LAYOUT


__all__ = ["VisionCriticStageHandler"]
