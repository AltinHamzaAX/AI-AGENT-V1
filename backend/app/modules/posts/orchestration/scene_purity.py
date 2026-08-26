from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.supervisor import SupervisorStage
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.generation import (
    GenerationPlan,
    SceneArtifact,
    SceneGenerationStatus,
)
from app.modules.posts.tools.scene_purity import (
    ScenePurityInput,
    ScenePurityInspector,
    ScenePurityReport,
    ScenePurityVerdict,
)

#: Sections a regeneration must leave untouched. The plate is the only thing
#: allowed to change; re-deciding strategy or copy would be a different post.
PRESERVED_ON_REGENERATION = (
    PostWorkflowSection.SEMANTIC_CONTRACT,
    PostWorkflowSection.COPY,
    PostWorkflowSection.DESIGN_SPEC,
    PostWorkflowSection.ASSETS,
    PostWorkflowSection.GENERATION_PLAN,
)


class ScenePurityStageHandler:
    """Certifies the generated plate before anything is allowed to compose it."""

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        max_regenerations: int = 2,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        if not 1 <= max_regenerations <= 5:
            raise ValueError("max_regenerations must be between 1 and 5")
        self._providers = providers
        self._max_regenerations = max_regenerations
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        state = context.workflow_state
        contract = _contract(state)
        history = list(_section(state, PostWorkflowSection.REVISION_HISTORY))
        requested = _regeneration_requests(history)
        artifact = _generated_scene(state)

        if artifact is None:
            report = ScenePurityReport.uninspected(
                contract_fingerprint=contract.fingerprint,
                reason=(
                    "No scene was generated for this post, so approved originals carry the "
                    "whole composition and there is nothing to contaminate it."
                ),
            )
        else:
            report = await self._inspect(context, contract, artifact, requested=requested)

        if report.verdict is ScenePurityVerdict.REGENERATE_SCENE:
            if requested >= self._max_regenerations:
                raise NonRetryableJobError(
                    f"scene purity still fails after {requested} regenerations: {report.reason}"
                )
            history = _request_regeneration(history, report)

        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.SCENE_PURITY: report.model_dump(mode="json"),
                PostWorkflowSection.REVISION_HISTORY: history,
            }
        )

    async def _inspect(
        self,
        context: SupervisorStageContext,
        contract: PostSemanticContract,
        artifact: SceneArtifact,
        *,
        requested: int,
    ) -> ScenePurityReport:
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
        if artifact.storage_key is None or artifact.mime_type is None:
            raise ValueError("a generated scene artifact must carry its storage metadata")
        state = context.workflow_state
        payload = ScenePurityInput(
            # The input validator re-hashes these bytes against the artifact's
            # checksum, so a plate that drifted in storage never gets inspected.
            scene_image=await providers.storage.get(key=artifact.storage_key),
            scene_mime_type=artifact.mime_type,
            scene_checksum=artifact.checksum or "",
            scene_storage_key=artifact.storage_key,
            semantic_contract=contract.to_dict(),
            generation_plan=GenerationPlan.model_validate(
                state.get(PostWorkflowSection.GENERATION_PLAN.value)
            ),
            asset_policies=[
                AssetPolicy.model_validate(item)
                for item in _section(state, PostWorkflowSection.ASSETS)
            ],
        )
        return await ScenePurityInspector(providers.vision).inspect(
            payload, regeneration_requests=requested
        )


def _contract(state: dict[str, Any]) -> PostSemanticContract:
    value = state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    if not isinstance(value, dict):
        raise ValueError("semantic_contract must be an object")
    return PostSemanticContract.from_dict(value)


def _section(state: dict[str, Any], section: PostWorkflowSection) -> list[Any]:
    value = state.get(section.value)
    if not isinstance(value, list):
        raise ValueError(f"{section.value} must be an array")
    return value


def _generated_scene(state: dict[str, Any]) -> SceneArtifact | None:
    generated = [
        artifact
        for item in _section(state, PostWorkflowSection.GENERATION_ARTIFACTS)
        for artifact in [SceneArtifact.model_validate(item)]
        if artifact.status is SceneGenerationStatus.GENERATED
    ]
    return generated[-1] if generated else None


def _regeneration_requests(history: list[Any]) -> int:
    return sum(
        1
        for entry in history
        if isinstance(entry, dict)
        and entry.get("requested_by") == SupervisorStage.SCENE_PURITY.value
    )


def _request_regeneration(history: list[Any], report: ScenePurityReport) -> list[Any]:
    entry = {
        "status": "pending",
        "target_stage": SupervisorStage.PRODUCTION.value,
        "requested_by": SupervisorStage.SCENE_PURITY.value,
        "reason": report.reason,
        "keep": [section.value for section in PRESERVED_ON_REGENERATION],
        "change": [PostWorkflowSection.GENERATION_ARTIFACTS.value],
        "contaminations": [finding.kind.value for finding in report.findings],
    }
    if history and history[-1] == entry:
        # A retried inspection of the same plate must not stack duplicate requests.
        return history
    return [*history, entry]


__all__ = ["PRESERVED_ON_REGENERATION", "ScenePurityStageHandler"]
