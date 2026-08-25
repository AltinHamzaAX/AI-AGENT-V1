from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    ExecutionTraceRecorder,
    completed_trace,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.research import (
    ExternalResearchInput,
    ExternalResearchService,
    InMemoryResearchCache,
    ResearchCache,
    ResearchCategoryMetrics,
    ResearchMetricsSink,
    ResearchStageMetrics,
    ResearchStatus,
)


class TraceResearchMetricsSink:
    """Writes research measurements onto the durable execution trace timeline.

    One record per category plus one for the stage, so the timeline answers
    "which category was slow, which came from cache, which degraded" without
    any separate metrics store. Values are counts and durations only; no
    query, source, or provider payload is recorded.
    """

    def __init__(
        self,
        recorder: ExecutionTraceRecorder,
        *,
        invocation: InvocationContext,
    ) -> None:
        self._recorder = recorder
        self._invocation = invocation

    async def record(self, metrics: ResearchStageMetrics) -> None:
        started_at = datetime.now(UTC) - timedelta(milliseconds=metrics.duration_ms)
        for item in metrics.categories:
            await self._recorder.record(
                completed_trace(
                    generation_id=self._invocation.generation_id,
                    correlation_id=self._invocation.correlation_id,
                    kind=ExecutionRunKind.TOOL,
                    name=f"research.{item.category.value}",
                    status=_trace_status(item),
                    started_at=started_at,
                    duration_ms=item.duration_ms,
                    error_code=item.error,
                    metadata=item.as_metadata(),
                )
            )
        await self._recorder.record(
            completed_trace(
                generation_id=self._invocation.generation_id,
                correlation_id=self._invocation.correlation_id,
                kind=ExecutionRunKind.GENERATION_STEP,
                name="research.stage",
                status=(
                    ExecutionRunStatus.FAILED
                    if metrics.succeeded == 0
                    else ExecutionRunStatus.SUCCEEDED
                ),
                started_at=started_at,
                duration_ms=metrics.duration_ms,
                metadata=metrics.as_metadata(),
            )
        )


def _trace_status(item: ResearchCategoryMetrics) -> ExecutionRunStatus:
    if item.status is ResearchStatus.FAILED:
        return (
            ExecutionRunStatus.TIMEOUT
            if item.error == "TimeoutError"
            else ExecutionRunStatus.FAILED
        )
    return ExecutionRunStatus.SUCCEEDED


class ExternalResearchStageHandler:
    """Runs all research tools and returns exactly the RESEARCH state section."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        cache: ResearchCache | None = None,
        cache_ttl_seconds: int = 3_600,
        max_concurrency: int = 4,
        search_timeout_seconds: float = 30.0,
        tool_timeout_seconds: float = 180.0,
        stage_timeout_seconds: float = 300.0,
        metrics_sink: ResearchMetricsSink | None = None,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._cache = cache or InMemoryResearchCache()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_concurrency = max_concurrency
        self._search_timeout_seconds = search_timeout_seconds
        self._tool_timeout_seconds = tool_timeout_seconds
        self._stage_timeout_seconds = stage_timeout_seconds
        self._metrics_sink = metrics_sink
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = _service_payload(context.workflow_state)
        invocation = InvocationContext(
            correlation_id=context.job_id,
            post_id=context.post_id,
            generation_id=context.generation_id,
        )
        metrics_sink = self._metrics_sink
        if metrics_sink is None and self._trace_recorder is not None:
            metrics_sink = TraceResearchMetricsSink(
                self._trace_recorder,
                invocation=invocation,
            )
        providers = self._providers
        if self._trace_recorder is not None:
            providers = trace_provider_bundle(
                providers,
                recorder=self._trace_recorder,
                invocation=invocation,
            )
        service = ExternalResearchService.from_providers(
            providers.research,
            providers.llm,
            cache=self._cache,
            cache_ttl_seconds=self._cache_ttl_seconds,
            max_concurrency=self._max_concurrency,
            search_timeout_seconds=self._search_timeout_seconds,
            tool_timeout_seconds=self._tool_timeout_seconds,
            stage_timeout_seconds=self._stage_timeout_seconds,
            metrics_sink=metrics_sink,
        )
        result = await service.run(payload)
        return SupervisorStageResult(
            outputs={PostWorkflowSection.RESEARCH: result.model_dump(mode="json")}
        )


def _service_payload(workflow_state: dict[str, Any]) -> ExternalResearchInput:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    audience_value = workflow_state.get(PostWorkflowSection.AUDIENCE.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    audience = AudienceIntelligence.model_validate(audience_value)
    return ExternalResearchInput(
        semantic_contract=contract.to_dict(),
        audience=audience,
    )


__all__ = ["ExternalResearchStageHandler", "TraceResearchMetricsSink"]
