from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
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
)


class ExternalResearchStageHandler:
    """Runs all research tools and returns exactly the RESEARCH state section."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        cache: ResearchCache | None = None,
        cache_ttl_seconds: int = 3_600,
        max_concurrency: int = 4,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._cache = cache or InMemoryResearchCache()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_concurrency = max_concurrency
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = _service_payload(context.workflow_state)
        invocation = InvocationContext(
            correlation_id=context.job_id,
            post_id=context.post_id,
            generation_id=context.generation_id,
        )
        providers = self._providers
        if self._trace_recorder is not None:
            providers = trace_provider_bundle(
                providers,
                recorder=self._trace_recorder,
                invocation=invocation,
            )
        service = ExternalResearchService.from_provider(
            providers.research,
            cache=self._cache,
            cache_ttl_seconds=self._cache_ttl_seconds,
            max_concurrency=self._max_concurrency,
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


__all__ = ["ExternalResearchStageHandler"]
