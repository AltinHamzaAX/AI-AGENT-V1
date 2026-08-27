from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.creative_director import (
    CREATIVE_DIRECTOR_AGENT_NAME,
    CreativeDirection,
    register_creative_director_agent,
)
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
from app.modules.posts.services.concept_memory import (
    ConceptMemoryService,
    PostMemoryScopeResolver,
)
from app.modules.posts.tools import ToolRegistry
from app.modules.posts.tools.research import ExternalResearchResult


class CreativeDirectionStageHandler:
    """Runs Creative Director and writes only the creative_concept section."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
        concept_memory: ConceptMemoryService | None = None,
        memory_scope_resolver: PostMemoryScopeResolver | None = None,
    ) -> None:
        if (concept_memory is None) != (memory_scope_resolver is None):
            raise ValueError(
                "concept_memory and memory_scope_resolver must be configured together"
            )
        self._providers = providers
        self._trace_recorder = trace_recorder
        self._concept_memory = concept_memory
        self._memory_scope_resolver = memory_scope_resolver

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = _agent_payload(context.workflow_state)
        memory_scope = None
        if self._concept_memory is not None and self._memory_scope_resolver is not None:
            memory_scope = await self._memory_scope_resolver.resolve_project_scope(
                post_id=context.post_id
            )
            if memory_scope is None:
                raise ValueError("creative concept memory scope could not resolve the post")
            payload["rejected_concept_memory"] = list(
                await self._concept_memory.recall_rejected(
                    scope=memory_scope,
                    query=_memory_query(payload),
                )
            )
            payload["recent_creative_patterns"] = [
                item.model_dump(mode="json")
                for item in await self._concept_memory.recall_approved(
                    scope=memory_scope,
                    query=_memory_query(payload),
                )
            ]
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
        register_creative_director_agent(runtime, providers.creative_llm)
        output = await runtime.run(
            CREATIVE_DIRECTOR_AGENT_NAME,
            payload,
            invocation=invocation,
        )
        if not isinstance(output, CreativeDirection):
            raise TypeError("creative director returned an invalid output type")
        if self._concept_memory is not None and memory_scope is not None:
            await self._concept_memory.remember_rejected(
                scope=memory_scope,
                direction=output,
                generation_id=context.generation_id,
            )
        return SupervisorStageResult(
            outputs={PostWorkflowSection.CREATIVE_CONCEPT: output.model_dump(mode="json")}
        )


def _agent_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    return {
        "marketing_strategy": MarketingStrategy.model_validate(
            workflow_state.get(PostWorkflowSection.MARKETING_STRATEGY.value)
        ).model_dump(mode="json"),
        "audience": AudienceIntelligence.model_validate(
            workflow_state.get(PostWorkflowSection.AUDIENCE.value)
        ).model_dump(mode="json"),
        "brand": BrandAnalysis.model_validate(
            workflow_state.get(PostWorkflowSection.BRAND.value)
        ).model_dump(mode="json"),
        "research": ExternalResearchResult.model_validate(
            workflow_state.get(PostWorkflowSection.RESEARCH.value)
        ).model_dump(mode="json"),
        "semantic_contract": contract.to_dict(),
    }


def _memory_query(payload: dict[str, Any]) -> str:
    strategy = payload["marketing_strategy"]
    brand = payload["brand"]
    contract = payload["semantic_contract"]
    return " ".join(
        (
            str(strategy["marketing_angle"]["decision"]),
            str(strategy["single_minded_message"]["decision"]),
            str(brand["identity_summary"]),
            str(contract["platform"]),
        )
    )


__all__ = ["CreativeDirectionStageHandler"]
