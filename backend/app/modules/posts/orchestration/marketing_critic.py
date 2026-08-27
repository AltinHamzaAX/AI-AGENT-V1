from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.marketing_critic import (
    MarketingCriticAgent,
    MarketingCriticDecision,
    MarketingCriticInput,
    MarketingCriticReport,
    MarketingIssueSeverity,
)
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
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


class MarketingCriticStageHandler:
    """Run marketing verification after composition and request a targeted revision."""

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
        strategy = MarketingStrategy.model_validate(
            _object(state, PostWorkflowSection.MARKETING_STRATEGY)
        )
        copy = CopyDraft.model_validate(_object(state, PostWorkflowSection.COPY))
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
        payload = MarketingCriticInput(
            final_image=final_bytes,
            final_mime_type=draft.final_asset.mime_type,
            semantic_contract=_object(state, PostWorkflowSection.SEMANTIC_CONTRACT),
            strategy=strategy,
            copy_draft=copy,
            post_draft=draft,
        )
        report = await MarketingCriticAgent(providers.vision).review(
            payload, revision_requests=requested
        )
        if report.decision is MarketingCriticDecision.REVISE:
            if requested >= self._max_revisions:
                raise NonRetryableJobError(
                    f"marketing quality still fails after {requested} revisions: {report.summary}"
                )
            history = _request_revision(history, report)
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.QUALITY: report.model_dump(mode="json"),
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
        and entry.get("requested_by") == SupervisorStage.QUALITY_REVIEW.value
    )


def _request_revision(history: list[Any], report: MarketingCriticReport) -> list[Any]:
    severity_order = {
        MarketingIssueSeverity.CRITICAL: 4,
        MarketingIssueSeverity.HIGH: 3,
        MarketingIssueSeverity.MEDIUM: 2,
        MarketingIssueSeverity.LOW: 1,
    }
    primary = max(report.issues, key=lambda issue: severity_order[issue.severity])
    changed_sections = {
        (
            PostWorkflowSection.COPY
            if issue.target_stage is SupervisorStage.COPYWRITING
            else PostWorkflowSection.MARKETING_STRATEGY
        )
        for issue in report.issues
    }
    entry = {
        "status": "pending",
        "target_stage": primary.target_stage.value,
        "requested_by": SupervisorStage.QUALITY_REVIEW.value,
        "reason": primary.reason,
        "recommended_action": primary.recommended_action,
        "keep": [
            PostWorkflowSection.SEMANTIC_CONTRACT.value,
            PostWorkflowSection.BRAND.value,
            PostWorkflowSection.PRODUCT.value,
            PostWorkflowSection.ASSETS.value,
            PostWorkflowSection.AUDIENCE.value,
            PostWorkflowSection.RESEARCH.value,
        ],
        "change": sorted(section.value for section in changed_sections),
        "issues": [issue.model_dump(mode="json") for issue in report.issues],
        "score": report.score,
        "render_fingerprint": report.render_fingerprint,
    }
    if history and history[-1] == entry:
        return history
    return [*history, entry]


__all__ = ["MarketingCriticStageHandler"]
