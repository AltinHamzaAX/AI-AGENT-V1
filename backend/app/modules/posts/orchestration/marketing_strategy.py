from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.agents.brand_product import BrandAnalysis, ProductAnalysis
from app.modules.posts.agents.client_understanding import ClientUnderstandingBrief
from app.modules.posts.agents.framework import AgentRuntime
from app.modules.posts.agents.marketing_strategist import (
    MARKETING_STRATEGIST_AGENT_NAME,
    MarketingStrategy,
    register_marketing_strategist_agent,
)
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
from app.modules.posts.tools.marketing import register_marketing_framework_tools
from app.modules.posts.tools.research import ExternalResearchResult


class MarketingStrategyStageHandler:
    """Runs the strategist and returns exactly the MARKETING_STRATEGY section."""

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
        tool_registry = ToolRegistry(trace_recorder=self._trace_recorder)
        register_marketing_framework_tools(tool_registry)
        runtime = AgentRuntime(
            tool_registry,
            trace_recorder=self._trace_recorder,
        )
        register_marketing_strategist_agent(runtime, providers.llm)
        output = await runtime.run(
            MARKETING_STRATEGIST_AGENT_NAME,
            payload,
            invocation=invocation,
        )
        if not isinstance(output, MarketingStrategy):
            raise TypeError("marketing strategist returned an invalid output type")
        return SupervisorStageResult(
            outputs={PostWorkflowSection.MARKETING_STRATEGY: output.model_dump(mode="json")}
        )


def _agent_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    """Assemble the five upstream sections this stage reasons over.

    Each is re-validated rather than trusted: workflow state is persisted
    between stages, so a section written by an older pipeline would otherwise
    reach the strategist as a plain dictionary.
    """
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    return {
        "brief": ClientUnderstandingBrief.model_validate(
            workflow_state.get(PostWorkflowSection.BRIEF.value)
        ).model_dump(mode="json"),
        "semantic_contract": contract.to_dict(),
        "brand": BrandAnalysis.model_validate(
            workflow_state.get(PostWorkflowSection.BRAND.value)
        ).model_dump(mode="json"),
        "product": ProductAnalysis.model_validate(
            workflow_state.get(PostWorkflowSection.PRODUCT.value)
        ).model_dump(mode="json"),
        "audience": AudienceIntelligence.model_validate(
            workflow_state.get(PostWorkflowSection.AUDIENCE.value)
        ).model_dump(mode="json"),
        "research": ExternalResearchResult.model_validate(
            workflow_state.get(PostWorkflowSection.RESEARCH.value)
        ).model_dump(mode="json"),
    }


__all__ = ["MarketingStrategyStageHandler"]
