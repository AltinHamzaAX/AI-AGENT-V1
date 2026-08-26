from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.art_director import (
    ART_DIRECTOR_AGENT_NAME,
    ArtDirection,
    ArtDirectorInput,
    register_art_director_agent,
)
from app.modules.posts.agents.asset_intelligence import AssetIntelligenceResult
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
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


class ArtDirectionStageHandler:
    """Runs Art Director and writes only the art-direction workflow section."""

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
        register_art_director_agent(runtime, providers.llm)
        output = await runtime.run(
            ART_DIRECTOR_AGENT_NAME,
            _agent_payload(context.workflow_state),
            invocation=invocation,
        )
        if not isinstance(output, ArtDirection):
            raise TypeError("art director returned an invalid output type")
        return SupervisorStageResult(
            outputs={PostWorkflowSection.ART_DIRECTION: output.model_dump(mode="json")}
        )


def _agent_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    return ArtDirectorInput(
        concept=CreativeDirection.model_validate(
            workflow_state.get(PostWorkflowSection.CREATIVE_CONCEPT.value)
        ),
        copy_draft=CopyDraft.model_validate(
            workflow_state.get(PostWorkflowSection.COPY.value)
        ),
        brand=BrandAnalysis.model_validate(
            workflow_state.get(PostWorkflowSection.BRAND.value)
        ),
        assets=AssetIntelligenceResult.model_validate(
            workflow_state.get(PostWorkflowSection.ASSETS.value)
        ),
        platform=contract.platform,
        semantic_contract=contract.to_dict(),
    ).model_dump(mode="json")


__all__ = ["ArtDirectionStageHandler"]
