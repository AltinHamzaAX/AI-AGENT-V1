from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents.asset_intelligence import (
    ASSET_INTELLIGENCE_AGENT_NAME,
    AssetIntelligenceInput,
    AssetIntelligenceResult,
    register_asset_intelligence_agent,
    validate_asset_intelligence_input,
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


class AssetIntelligenceStageHandler:
    """Classifies all attachments and returns exactly the ASSETS state section."""

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
        register_asset_intelligence_agent(runtime, providers.llm)
        output = await runtime.run(
            ASSET_INTELLIGENCE_AGENT_NAME,
            payload,
            invocation=invocation,
        )
        if not isinstance(output, AssetIntelligenceResult):
            raise TypeError("asset intelligence returned an invalid output type")
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.ASSETS: [
                    policy.model_dump(mode="json") for policy in output.assets
                ]
            }
        )


def _agent_payload(workflow_state: dict[str, Any]) -> AssetIntelligenceInput:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    conversation = workflow_state.get(PostWorkflowSection.CONVERSATION_CONTEXT.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    if not isinstance(conversation, dict):
        raise ValueError("conversation_context must be an object")
    contract = PostSemanticContract.from_dict(contract_value)
    raw_history = conversation.get("conversation_history", [])
    if not isinstance(raw_history, list):
        raise ValueError("conversation_history must be an array")
    history = []
    for turn in raw_history:
        if not isinstance(turn, dict) or not isinstance(turn.get("content"), str):
            raise ValueError("conversation history entries must contain text content")
        role = turn.get("role", "unknown")
        history.append(f"{role}: {turn['content']}")
    attachments = conversation.get("attachments", [])
    if not isinstance(attachments, list):
        raise ValueError("conversation attachments must be an array")
    normalized_attachments = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("conversation attachment must be an object")
        normalized = dict(attachment)
        if "declared_role" not in normalized and "role" in normalized:
            normalized["declared_role"] = normalized.pop("role")
        normalized_attachments.append(normalized)
    latest_message = conversation.get("latest_message", "")
    if not isinstance(latest_message, str):
        raise ValueError("latest_message must be text")
    payload = AssetIntelligenceInput(
        semantic_contract=contract.to_dict(),
        latest_message=latest_message,
        conversation_history=history,
        attachments=normalized_attachments,
    )
    validate_asset_intelligence_input(payload)
    return payload


__all__ = ["AssetIntelligenceStageHandler"]
