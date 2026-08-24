import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from pydantic import BaseModel

from app.modules.posts.domain.contracts import (
    AgentDefinition,
    InvocationContext,
    RetryPolicy,
    ToolCapability,
    ToolDefinition,
)
from app.modules.posts.domain.exceptions import (
    DuplicateRegistrationError,
    InvocationFailedError,
    InvocationTimeoutError,
    ToolNotFoundError,
    UnauthorizedToolInvocationError,
)
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    ExecutionTraceCreate,
    ExecutionTraceRecorder,
    safe_error_code,
    trace_reference,
)

logger = logging.getLogger(__name__)

ToolHandler = Callable[[BaseModel, "ToolExecutionContext"], Awaitable[BaseModel | dict[str, Any]]]

_MANDATORY_DENIED_CAPABILITIES: dict[str, frozenset[ToolCapability]] = {
    "creative_director": frozenset(
        {
            ToolCapability.FINAL_APPROVAL,
            ToolCapability.DATABASE_MUTATION,
            ToolCapability.ASSET_REPLACEMENT,
        }
    )
}


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    invocation: InvocationContext
    agent_name: str
    tool_name: str
    attempt: int


class ToolGateway(Protocol):
    async def invoke(self, tool_name: str, payload: BaseModel | dict[str, Any]) -> BaseModel: ...


@dataclass(frozen=True, slots=True)
class _RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, *, trace_recorder: ExecutionTraceRecorder | None = None) -> None:
        self._tools: dict[str, _RegisteredTool] = {}
        self._agent_tokens: dict[str, tuple[AgentDefinition, object]] = {}
        self._trace_recorder = trace_recorder

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.tool_name in self._tools:
            raise DuplicateRegistrationError(f"Tool '{definition.tool_name}' is already registered")
        self._tools[definition.tool_name] = _RegisteredTool(definition, handler)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(registered.definition for _, registered in sorted(self._tools.items()))

    def get_definition(self, tool_name: str) -> ToolDefinition:
        try:
            return self._tools[tool_name].definition
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not registered") from exc

    def _bind_agent(
        self,
        definition: AgentDefinition,
        token: object,
        invocation: InvocationContext,
    ) -> ToolGateway:
        existing = self._agent_tokens.get(definition.name)
        if existing is not None and existing != (definition, token):
            raise DuplicateRegistrationError(f"Agent identity '{definition.name}' is already bound")
        self._agent_tokens[definition.name] = (definition, token)
        return _BoundToolGateway(self, definition, token, invocation)

    async def _invoke(
        self,
        *,
        agent: AgentDefinition,
        token: object,
        invocation: InvocationContext,
        tool_name: str,
        payload: BaseModel | dict[str, Any],
    ) -> BaseModel:
        try:
            registered = self._tools[tool_name]
        except KeyError as exc:
            await self._record_trace(
                tool_name,
                invocation,
                status=ExecutionRunStatus.FAILED,
                started_at=datetime.now(UTC),
                duration_ms=0,
                input_reference=trace_reference(payload),
                error_code="ToolNotFoundError",
                metadata={"agent_name": agent.name},
            )
            raise ToolNotFoundError(f"Tool '{tool_name}' is not registered") from exc
        try:
            self._authorize(
                agent=agent,
                token=token,
                tool=registered.definition,
                invocation=invocation,
            )
        except UnauthorizedToolInvocationError as exc:
            await self._record_trace(
                registered.definition.tool_name,
                invocation,
                status=ExecutionRunStatus.DENIED,
                started_at=datetime.now(UTC),
                duration_ms=0,
                input_reference=trace_reference(payload),
                error_code=safe_error_code(exc),
                metadata={"agent_name": agent.name},
            )
            raise
        try:
            validated_input = registered.definition.input_schema.model_validate(payload)
        except Exception as exc:
            await self._record_trace(
                registered.definition.tool_name,
                invocation,
                status=ExecutionRunStatus.FAILED,
                started_at=datetime.now(UTC),
                duration_ms=0,
                input_reference=trace_reference(payload),
                error_code=safe_error_code(exc),
                metadata={"agent_name": agent.name},
            )
            raise
        return await self._execute(
            registered=registered,
            agent=agent,
            invocation=invocation,
            validated_input=validated_input,
        )

    def _authorize(
        self,
        *,
        agent: AgentDefinition,
        token: object,
        tool: ToolDefinition,
        invocation: InvocationContext,
    ) -> None:
        bound = self._agent_tokens.get(agent.name)
        reason: str | None = None
        if bound is None or bound[0] != agent or bound[1] is not token:
            reason = "unregistered agent identity"
        elif tool.tool_name not in agent.allowed_tools:
            reason = "tool is not in agent.allowed_tools"
        elif agent.name not in tool.allowed_agents:
            reason = "agent is not in tool.allowed_agents"
        else:
            denied = _MANDATORY_DENIED_CAPABILITIES.get(agent.name, frozenset())
            prohibited = denied & tool.security.capabilities
            if prohibited:
                capabilities = ", ".join(sorted(capability.value for capability in prohibited))
                reason = f"security policy denies capabilities: {capabilities}"
        if reason is None:
            return
        logger.warning(
            "posts.tool.denied",
            extra={
                "event": "posts.tool.denied",
                "agent_name": agent.name,
                "tool_name": tool.tool_name,
                "denial_reason": reason,
                "correlation_id": str(invocation.correlation_id),
                "post_id": str(invocation.post_id) if invocation.post_id else None,
                "generation_id": (
                    str(invocation.generation_id) if invocation.generation_id else None
                ),
            },
        )
        raise UnauthorizedToolInvocationError(
            agent_name=agent.name,
            tool_name=tool.tool_name,
            reason=reason,
        )

    async def _execute(
        self,
        *,
        registered: _RegisteredTool,
        agent: AgentDefinition,
        invocation: InvocationContext,
        validated_input: BaseModel,
    ) -> BaseModel:
        definition = registered.definition
        policy = definition.retry_policy
        started_at = monotonic()
        started_wall = datetime.now(UTC)
        input_reference = trace_reference(validated_input)
        for attempt in range(1, policy.max_attempts + 1):
            context = ToolExecutionContext(
                invocation=invocation,
                agent_name=agent.name,
                tool_name=definition.tool_name,
                attempt=attempt,
            )
            try:
                raw_output = await asyncio.wait_for(
                    registered.handler(validated_input, context),
                    timeout=definition.timeout_seconds,
                )
                output = definition.output_schema.model_validate(raw_output)
            except TimeoutError as exc:
                if _can_retry(policy, attempt=attempt, timeout=True):
                    _log_retry(agent.name, definition.tool_name, invocation, attempt, "timeout")
                    await _backoff(policy)
                    continue
                _log_completion(
                    "posts.tool.timeout",
                    agent.name,
                    definition.tool_name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.tool_name,
                    invocation,
                    status=ExecutionRunStatus.TIMEOUT,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                    metadata={"agent_name": agent.name},
                )
                raise InvocationTimeoutError(
                    component="tool",
                    name=definition.tool_name,
                    attempts=attempt,
                ) from exc
            except Exception as exc:
                if _can_retry(policy, attempt=attempt, timeout=False):
                    _log_retry(agent.name, definition.tool_name, invocation, attempt, "error")
                    await _backoff(policy)
                    continue
                _log_completion(
                    "posts.tool.failed",
                    agent.name,
                    definition.tool_name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.tool_name,
                    invocation,
                    status=ExecutionRunStatus.FAILED,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                    metadata={"agent_name": agent.name},
                )
                raise InvocationFailedError(
                    component="tool",
                    name=definition.tool_name,
                    attempts=attempt,
                ) from exc
            _log_completion(
                "posts.tool.succeeded",
                agent.name,
                definition.tool_name,
                invocation,
                attempt,
                started_at,
            )
            await self._record_trace(
                definition.tool_name,
                invocation,
                status=ExecutionRunStatus.SUCCEEDED,
                started_at=started_wall,
                duration_ms=_duration_ms(started_at),
                retry_count=attempt - 1,
                input_reference=input_reference,
                output_reference=trace_reference(output),
                metadata={"agent_name": agent.name},
            )
            return output
        raise AssertionError("unreachable retry loop")

    async def _record_trace(
        self,
        name: str,
        invocation: InvocationContext,
        **fields: Any,
    ) -> None:
        if self._trace_recorder is None or invocation.generation_id is None:
            return
        try:
            await self._trace_recorder.record(
                ExecutionTraceCreate(
                    generation_id=invocation.generation_id,
                    correlation_id=invocation.correlation_id,
                    kind=ExecutionRunKind.TOOL,
                    name=name,
                    **fields,
                )
            )
        except Exception:  # noqa: BLE001 - telemetry must not break tool execution
            logger.exception("posts.trace.record_failed", extra={"trace_kind": "tool"})


class _BoundToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        agent: AgentDefinition,
        token: object,
        invocation: InvocationContext,
    ) -> None:
        self._registry = registry
        self._agent = agent
        self._token = token
        self._invocation = invocation

    async def invoke(
        self,
        tool_name: str,
        payload: BaseModel | dict[str, Any],
    ) -> BaseModel:
        return await self._registry._invoke(
            agent=self._agent,
            token=self._token,
            invocation=self._invocation,
            tool_name=tool_name,
            payload=payload,
        )


def _can_retry(policy: RetryPolicy, *, attempt: int, timeout: bool) -> bool:
    enabled = policy.retry_on_timeout if timeout else policy.retry_on_error
    return enabled and attempt < policy.max_attempts


async def _backoff(policy: RetryPolicy) -> None:
    if policy.backoff_seconds:
        await asyncio.sleep(policy.backoff_seconds)


def _log_retry(
    agent_name: str,
    tool_name: str,
    invocation: InvocationContext,
    attempt: int,
    reason: str,
) -> None:
    logger.info(
        "posts.tool.retry",
        extra={
            "event": "posts.tool.retry",
            "agent_name": agent_name,
            "tool_name": tool_name,
            "correlation_id": str(invocation.correlation_id),
            "attempt": attempt,
            "retry_reason": reason,
        },
    )


def _log_completion(
    event: str,
    agent_name: str,
    tool_name: str,
    invocation: InvocationContext,
    attempt: int,
    started_at: float,
) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "agent_name": agent_name,
            "tool_name": tool_name,
            "correlation_id": str(invocation.correlation_id),
            "attempt": attempt,
            "duration_ms": round((monotonic() - started_at) * 1000, 3),
        },
    )


def _duration_ms(started_at: float) -> int:
    return max(0, round((monotonic() - started_at) * 1000))


__all__ = ["ToolExecutionContext", "ToolGateway", "ToolHandler", "ToolRegistry"]
