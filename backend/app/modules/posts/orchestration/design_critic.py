from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.design_critic import (
    DesignCriticDecision,
    DesignCriticInput,
    DesignCriticReport,
    DesignIssueSeverity,
    SeniorDesignCritic,
)
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.composition import PostDraft


class DesignCriticStageHandler:
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
        requested = _revision_requests(history)
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
        final_bytes = await providers.storage.get(key=draft.final_asset.storage_key)
        payload = DesignCriticInput(
            final_image=final_bytes,
            final_mime_type=draft.final_asset.mime_type,
            semantic_contract=_object(state, PostWorkflowSection.SEMANTIC_CONTRACT),
            art_direction=ArtDirection.model_validate(
                _object(state, PostWorkflowSection.ART_DIRECTION)
            ),
            design_spec=DesignSpec.model_validate(_object(state, PostWorkflowSection.DESIGN_SPEC)),
            post_draft=draft,
        )
        report = await SeniorDesignCritic(providers.vision).review(
            payload, revision_requests=requested
        )
        if report.decision is DesignCriticDecision.REVISE:
            if requested >= self._max_revisions:
                raise NonRetryableJobError(
                    f"design quality still fails after {requested} revisions: {report.summary}"
                )
            history = _request_revision(history, report)
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.DESIGN_QUALITY: report.model_dump(mode="json"),
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
        1
        for entry in history
        if isinstance(entry, dict)
        and entry.get("requested_by") == SupervisorStage.DESIGN_REVIEW.value
    )


def _request_revision(history: list[Any], report: DesignCriticReport) -> list[Any]:
    severity_order = {
        DesignIssueSeverity.CRITICAL: 4,
        DesignIssueSeverity.HIGH: 3,
        DesignIssueSeverity.MEDIUM: 2,
        DesignIssueSeverity.LOW: 1,
    }
    primary = max(report.problems, key=lambda problem: severity_order[problem.severity])
    changed_sections = {
        {
            SupervisorStage.CREATIVE_CONCEPT: PostWorkflowSection.CREATIVE_CONCEPT,
            SupervisorStage.ART_DIRECTION: PostWorkflowSection.ART_DIRECTION,
            SupervisorStage.DESIGN_SPEC: PostWorkflowSection.DESIGN_SPEC,
        }[problem.target_stage]
        for problem in report.problems
    }
    entry = {
        "status": "pending",
        "target_stage": primary.target_stage.value,
        "requested_by": SupervisorStage.DESIGN_REVIEW.value,
        "reason": primary.cause,
        "recommended_action": primary.recommended_change,
        "location": primary.location,
        "keep": [
            PostWorkflowSection.SEMANTIC_CONTRACT.value,
            PostWorkflowSection.BRAND.value,
            PostWorkflowSection.PRODUCT.value,
            PostWorkflowSection.ASSETS.value,
            PostWorkflowSection.MARKETING_STRATEGY.value,
            PostWorkflowSection.COPY.value,
        ],
        "change": sorted(section.value for section in changed_sections),
        "problems": [problem.model_dump(mode="json") for problem in report.problems],
        "render_fingerprint": report.render_fingerprint,
    }
    if history and history[-1] == entry:
        return history
    return [*history, entry]


__all__ = ["DesignCriticStageHandler"]
