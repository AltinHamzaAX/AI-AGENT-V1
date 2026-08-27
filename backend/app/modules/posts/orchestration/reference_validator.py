from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.agents.reference_validator import (
    ReferenceDecision,
    ReferenceDimension,
    ReferenceOriginalityValidator,
    ReferenceValidationReport,
    ReferenceValidatorInput,
)
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import SupervisorStageContext, SupervisorStageResult
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.research import ExternalResearchResult
from app.modules.posts.tools.revision import RevisionDirector, RevisionFinding, RevisionRoute


class ReferenceValidatorStageHandler:
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
        history = list(_array(state, PostWorkflowSection.REVISION_HISTORY))
        requested = sum(
            1
            for item in history
            if isinstance(item, dict)
            and item.get("requested_by") == SupervisorStage.REFERENCE_VALIDATION.value
        )
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
        report = await ReferenceOriginalityValidator(providers.llm).review(
            ReferenceValidatorInput(
                semantic_contract=_object(state, PostWorkflowSection.SEMANTIC_CONTRACT),
                brand=BrandAnalysis.model_validate(_object(state, PostWorkflowSection.BRAND)),
                research=ExternalResearchResult.model_validate(
                    _object(state, PostWorkflowSection.RESEARCH)
                ),
                marketing_strategy=MarketingStrategy.model_validate(
                    _object(state, PostWorkflowSection.MARKETING_STRATEGY)
                ),
                creative_direction=CreativeDirection.model_validate(
                    _object(state, PostWorkflowSection.CREATIVE_CONCEPT)
                ),
                copy_draft=CopyDraft.model_validate(_object(state, PostWorkflowSection.COPY)),
                art_direction=ArtDirection.model_validate(
                    _object(state, PostWorkflowSection.ART_DIRECTION)
                ),
                design_spec=DesignSpec.model_validate(
                    _object(state, PostWorkflowSection.DESIGN_SPEC)
                ),
            ),
            revision_requests=requested,
        )
        if report.decision is ReferenceDecision.REVISE:
            if requested >= self._max_revisions:
                raise NonRetryableJobError(
                    "reference originality still fails after "
                    f"{requested} revisions: {report.summary}"
                )
            history = _request_revision(history, report)
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.REFERENCE_VALIDATION: report.model_dump(mode="json"),
                PostWorkflowSection.REVISION_HISTORY: history,
            }
        )


def _request_revision(history: list[Any], report: ReferenceValidationReport) -> list[Any]:
    findings = [
        RevisionFinding(
            route=_route(issue.dimensions),
            why=issue.observed,
            action=issue.recommended_action,
            location=issue.region,
            source="reference_validator:" + ",".join(item.value for item in issue.dimensions),
        )
        for issue in report.issues
    ]
    director = RevisionDirector()
    instruction = director.plan(
        findings,
        requested_by=SupervisorStage.REFERENCE_VALIDATION,
        history=history,
    )
    return director.append(history, instruction)


def _route(dimensions: list[ReferenceDimension]) -> RevisionRoute:
    values = set(dimensions)
    if ReferenceDimension.MARKET_FIT in values:
        return RevisionRoute.STRATEGY
    if ReferenceDimension.LAYOUT_SIMILARITY in values:
        return RevisionRoute.LAYOUT
    return RevisionRoute.CONCEPT


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


__all__ = ["ReferenceValidatorStageHandler"]
