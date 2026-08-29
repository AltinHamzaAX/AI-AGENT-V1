from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.generation import (
    GenerationDecision,
    GenerationPlan,
    ImagePromptBuilder,
    SceneArtifact,
    SceneGenerationStatus,
    SceneGenerator,
    ScenePromptInput,
)


class ProductionStageHandler:
    """Generate only a scene plate, persist it, and write artifact metadata."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        payload = _production_payload(context.workflow_state)
        if payload.generation_plan.decision is GenerationDecision.COMPOSE_ONLY:
            artifact = SceneArtifact(
                status=SceneGenerationStatus.SKIPPED,
                kind=None,
                reason="Approved assets already provide the scene; image generation skipped.",
            )
        else:
            prompt = ImagePromptBuilder().build(payload)
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
            artifact = await SceneGenerator(providers.image, providers.storage).generate(
                prompt,
                storage_key=(
                    f"posts/{context.post_id}/generations/{context.generation_id}/"
                    f"scene-{prompt.prompt_fingerprint}.image"
                ),
            )
        return SupervisorStageResult(
            outputs={PostWorkflowSection.GENERATION_ARTIFACTS: [artifact.model_dump(mode="json")]}
        )


def _production_payload(workflow_state: dict[str, Any]) -> ScenePromptInput:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    assets_value = workflow_state.get(PostWorkflowSection.ASSETS.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    if not isinstance(assets_value, list):
        raise ValueError("assets must be an array")
    contract = PostSemanticContract.from_dict(contract_value)
    return ScenePromptInput(
        semantic_contract=contract.to_dict(),
        creative_concept=CreativeDirection.model_validate(
            workflow_state.get(PostWorkflowSection.CREATIVE_CONCEPT.value)
        ),
        art_direction=ArtDirection.model_validate(
            workflow_state.get(PostWorkflowSection.ART_DIRECTION.value)
        ),
        design_spec=DesignSpec.model_validate(
            workflow_state.get(PostWorkflowSection.DESIGN_SPEC.value)
        ),
        asset_policies=[AssetPolicy.model_validate(item) for item in assets_value],
        generation_plan=GenerationPlan.model_validate(
            workflow_state.get(PostWorkflowSection.GENERATION_PLAN.value)
        ),
    )


__all__ = ["ProductionStageHandler"]
