from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.audience_research import (
    AUDIENCE_INTELLIGENCE_AGENT_NAME,
    AudienceIntelligence,
    AudienceIntelligenceInput,
    register_audience_intelligence_agent,
    validate_audience_intelligence_input,
)
from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis
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


class AudienceIntelligenceStageHandler:
    """Runs the specialist and returns exactly the AUDIENCE state section."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = _agent_payload(context.workflow_state)
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
        register_audience_intelligence_agent(runtime, providers.llm)
        output = await runtime.run(
            AUDIENCE_INTELLIGENCE_AGENT_NAME,
            payload,
            invocation=invocation,
        )
        if not isinstance(output, AudienceIntelligence):
            raise TypeError("audience intelligence returned an invalid output type")
        return SupervisorStageResult(
            outputs={PostWorkflowSection.AUDIENCE: output.model_dump(mode="json")}
        )


def _agent_payload(workflow_state: dict[str, Any]) -> AudienceIntelligenceInput:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    brand_value = workflow_state.get(PostWorkflowSection.BRAND.value)
    product_value = workflow_state.get(PostWorkflowSection.PRODUCT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    brand = BrandAnalysis.model_validate(brand_value)
    product = ProductAnalysis.model_validate(product_value)
    payload = AudienceIntelligenceInput(
        semantic_contract=contract.to_dict(),
        brand=brand,
        product=product,
    )
    validate_audience_intelligence_input(payload)
    return payload


__all__ = ["AudienceIntelligenceStageHandler"]
