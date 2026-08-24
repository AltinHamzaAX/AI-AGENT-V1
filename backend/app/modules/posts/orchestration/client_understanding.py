from typing import Any

from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents import AgentRuntime
from app.modules.posts.agents.client_understanding import (
    CLIENT_UNDERSTANDING_AGENT_NAME,
    ClientUnderstandingBrief,
    register_client_understanding_agent,
)
from app.modules.posts.domain.clarification import ClarificationEngine
from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.observability import ExecutionTraceRecorder
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.tools import ToolRegistry


class ClientUnderstandingStageHandler:
    """Adapts the specialist AgentRuntime output to the Supervisor BRIEF section."""

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
        tools = ToolRegistry(trace_recorder=self._trace_recorder)
        runtime = AgentRuntime(tools, trace_recorder=self._trace_recorder)
        register_client_understanding_agent(runtime, providers.llm)
        output = await runtime.run(
            CLIENT_UNDERSTANDING_AGENT_NAME,
            _agent_payload(context.workflow_state),
            invocation=invocation,
        )
        if not isinstance(output, ClientUnderstandingBrief):
            raise TypeError("client understanding returned an invalid output type")
        brief = output.model_dump(mode="json")
        brief["clarification"] = ClarificationEngine().evaluate(output).model_dump(mode="json")
        return SupervisorStageResult(
            outputs={
                PostWorkflowSection.BRIEF: brief,
            }
        )


def _agent_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    conversation_context = workflow_state.get(PostWorkflowSection.CONVERSATION_CONTEXT.value)
    if not isinstance(conversation_context, dict):
        raise ValueError("conversation_context must be an object")
    return {
        "conversation_history": conversation_context.get("conversation_history", []),
        "latest_message": conversation_context.get("latest_message"),
        "attachments": conversation_context.get(
            "attachments",
            workflow_state.get(PostWorkflowSection.ASSETS.value, []),
        ),
        "project_context": conversation_context.get("project_context", {}),
    }


__all__ = ["ClientUnderstandingStageHandler"]
