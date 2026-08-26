from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import (
    COPYWRITER_AGENT_NAME,
    CopyDraft,
    CopywriterInput,
    register_copywriter_agent,
)
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.framework import AgentRuntime
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools import ToolRegistry


class CopywritingStageHandler:
    """Runs Copywriter and writes only the copy workflow section."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
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
        runtime = AgentRuntime(
            ToolRegistry(trace_recorder=self._trace_recorder),
            trace_recorder=self._trace_recorder,
        )
        register_copywriter_agent(runtime, providers.llm)
        output = await runtime.run(
            COPYWRITER_AGENT_NAME,
            _agent_payload(context.workflow_state),
            invocation=invocation,
        )
        if not isinstance(output, CopyDraft):
            raise TypeError("copywriter returned an invalid output type")
        return SupervisorStageResult(
            outputs={PostWorkflowSection.COPY: output.model_dump(mode="json")}
        )


def _agent_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    return CopywriterInput(
        strategy=MarketingStrategy.model_validate(
            workflow_state.get(PostWorkflowSection.MARKETING_STRATEGY.value)
        ),
        concept=CreativeDirection.model_validate(
            workflow_state.get(PostWorkflowSection.CREATIVE_CONCEPT.value)
        ),
        brand_voice=BrandAnalysis.model_validate(
            workflow_state.get(PostWorkflowSection.BRAND.value)
        ),
        platform=contract.platform,
        offer=contract.offer,
        semantic_contract=contract.to_dict(),
    ).model_dump(mode="json")


__all__ = ["CopywritingStageHandler"]
