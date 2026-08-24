from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.brand_product import (
    BRAND_PRODUCT_AGENT_NAME,
    BrandProductAnalysis,
    register_brand_product_agent,
)
from app.modules.posts.agents.framework import AgentRuntime
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


class BrandProductStageHandler:
    """Runs the specialist and returns exactly the BRAND and PRODUCT sections."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        semantic_contract = _semantic_contract(context.workflow_state)
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
        register_brand_product_agent(runtime, providers.llm)
        output = await runtime.run(
            BRAND_PRODUCT_AGENT_NAME,
            {"semantic_contract": semantic_contract.to_dict()},
            invocation=invocation,
        )
        if not isinstance(output, BrandProductAnalysis):
            raise TypeError("brand product strategist returned an invalid output type")
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.BRAND: output.brand.model_dump(mode="json"),
                PostWorkflowSection.PRODUCT: output.product.model_dump(mode="json"),
            }
        )


def _semantic_contract(workflow_state: dict[str, Any]) -> PostSemanticContract:
    value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(value, dict):
        raise ValueError("semantic_contract must be an object")
    return PostSemanticContract.from_dict(value)


__all__ = ["BrandProductStageHandler"]
