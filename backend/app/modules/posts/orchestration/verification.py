from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools.composition import PostDraft
from app.modules.posts.tools.verification import (
    HardVerificationGate,
    VerificationInput,
)


class VerificationStageHandler:
    """Run the hard gates on the finished render and record what they decided.

    This stage never requests a revision. A hard gate that could be negotiated
    with is a score, and the layer exists precisely so that some failures are not
    negotiable: the Supervisor reads a BLOCKED report and stops the workflow.
    """

    def __init__(
        self,
        providers: ProviderBundle,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._trace_recorder = trace_recorder

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        state = context.workflow_state
        draft = PostDraft.model_validate(_object(state, PostWorkflowSection.POST_DRAFT))
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
        payload = VerificationInput(
            # The input validator re-hashes these bytes against the draft's
            # checksum, so a render that drifted in storage is never certified.
            final_image=await providers.storage.get(key=draft.final_asset.storage_key),
            final_mime_type=draft.final_asset.mime_type,
            semantic_contract=_object(state, PostWorkflowSection.SEMANTIC_CONTRACT),
            copy_draft=CopyDraft.model_validate(_object(state, PostWorkflowSection.COPY)),
            design_spec=DesignSpec.model_validate(_object(state, PostWorkflowSection.DESIGN_SPEC)),
            post_draft=draft,
            asset_policies=[
                AssetPolicy.model_validate(item)
                for item in _array(state, PostWorkflowSection.ASSETS)
            ],
        )
        report = await HardVerificationGate(providers.vision).verify(payload)
        return SupervisorStageResult(
            outputs={PostWorkflowSection.VERIFICATION: report.model_dump(mode="json")}
        )


def _object(state: dict[str, Any], section: PostWorkflowSection) -> dict[str, Any]:
    value = state.get(section.value)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{section.value} must be a populated object")
    return value


def _array(state: dict[str, Any], section: PostWorkflowSection) -> list[Any]:
    value = state.get(section.value)
    if not isinstance(value, list):
        raise ValueError(f"{section.value} must be an array")
    return value


__all__ = ["VerificationStageHandler"]
