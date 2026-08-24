import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from pydantic import BaseModel

from app.modules.posts.domain.contracts import AgentDefinition, InvocationContext, RetryPolicy
from app.modules.posts.domain.exceptions import (
    AgentNotFoundError,
    AgentToolFrameworkError,
    DuplicateRegistrationError,
    InvocationFailedError,
    InvocationTimeoutError,
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
from app.modules.posts.tools import ToolGateway, ToolRegistry

logger = logging.getLogger(__name__)

AgentHandler = Callable[
    [BaseModel, ToolGateway, "AgentExecutionContext"],
    Awaitable[BaseModel | dict[str, Any]],
]


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    invocation: InvocationContext
    agent_name: str
    attempt: int


@dataclass(frozen=True, slots=True)
class _RegisteredAgent:
    definition: AgentDefinition
    handler: AgentHandler
    identity_token: object


class AgentRuntime:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        trace_recorder: ExecutionTraceRecorder | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._trace_recorder = trace_recorder
        self._agents: dict[str, _RegisteredAgent] = {}

    def register(self, definition: AgentDefinition, handler: AgentHandler) -> None:
        if definition.name in self._agents:
            raise DuplicateRegistrationError(f"Agent '{definition.name}' is already registered")
        self._agents[definition.name] = _RegisteredAgent(
            definition=definition,
            handler=handler,
            identity_token=object(),
        )

    def definitions(self) -> tuple[AgentDefinition, ...]:
        return tuple(registered.definition for _, registered in sorted(self._agents.items()))

    def get_definition(self, agent_name: str) -> AgentDefinition:
        try:
            return self._agents[agent_name].definition
        except KeyError as exc:
            raise AgentNotFoundError(f"Agent '{agent_name}' is not registered") from exc

    async def run(
        self,
        agent_name: str,
        payload: BaseModel | dict[str, Any],
        *,
        invocation: InvocationContext | None = None,
    ) -> BaseModel:
        context = invocation or InvocationContext()
        try:
            registered = self._agents[agent_name]
        except KeyError as exc:
            await self._record_trace(
                agent_name,
                context,
                status=ExecutionRunStatus.FAILED,
                started_at=datetime.now(UTC),
                duration_ms=0,
                input_reference=trace_reference(payload),
                error_code="AgentNotFoundError",
            )
            raise AgentNotFoundError(f"Agent '{agent_name}' is not registered") from exc
        try:
            validated_input = registered.definition.input_schema.model_validate(payload)
        except Exception as exc:
            await self._record_trace(
                agent_name,
                context,
                status=ExecutionRunStatus.FAILED,
                started_at=datetime.now(UTC),
                duration_ms=0,
                input_reference=trace_reference(payload),
                error_code=safe_error_code(exc),
            )
            raise
        return await self._execute(
            registered=registered,
            invocation=context,
            validated_input=validated_input,
        )

    async def _execute(
        self,
        *,
        registered: _RegisteredAgent,
        invocation: InvocationContext,
        validated_input: BaseModel,
    ) -> BaseModel:
        definition = registered.definition
        policy = definition.retry_policy
        started_at = monotonic()
        started_wall = datetime.now(UTC)
        input_reference = trace_reference(validated_input)
        logger.info(
            "posts.agent.started",
            extra=_agent_log_fields(
                "posts.agent.started",
                definition.name,
                invocation,
                attempt=1,
            ),
        )
        for attempt in range(1, policy.max_attempts + 1):
            gateway = self._tool_registry._bind_agent(
                definition,
                registered.identity_token,
                invocation,
            )
            execution_context = AgentExecutionContext(
                invocation=invocation,
                agent_name=definition.name,
                attempt=attempt,
            )
            try:
                raw_output = await asyncio.wait_for(
                    registered.handler(validated_input, gateway, execution_context),
                    timeout=definition.timeout_seconds,
                )
                output = definition.output_schema.model_validate(raw_output)
            except TimeoutError as exc:
                if _can_retry(policy, attempt=attempt, timeout=True):
                    _log_retry(definition.name, invocation, attempt, "timeout")
                    await _backoff(policy)
                    continue
                _log_completion(
                    "posts.agent.timeout",
                    definition.name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.name,
                    invocation,
                    status=ExecutionRunStatus.TIMEOUT,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                )
                raise InvocationTimeoutError(
                    component="agent",
                    name=definition.name,
                    attempts=attempt,
                ) from exc
            except UnauthorizedToolInvocationError as exc:
                _log_completion(
                    "posts.agent.failed",
                    definition.name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.name,
                    invocation,
                    status=ExecutionRunStatus.DENIED,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                )
                raise
            except AgentToolFrameworkError as exc:
                _log_completion(
                    "posts.agent.failed",
                    definition.name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.name,
                    invocation,
                    status=ExecutionRunStatus.FAILED,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                )
                raise
            except Exception as exc:
                if _can_retry(policy, attempt=attempt, timeout=False):
                    _log_retry(definition.name, invocation, attempt, "error")
                    await _backoff(policy)
                    continue
                _log_completion(
                    "posts.agent.failed",
                    definition.name,
                    invocation,
                    attempt,
                    started_at,
                )
                await self._record_trace(
                    definition.name,
                    invocation,
                    status=ExecutionRunStatus.FAILED,
                    started_at=started_wall,
                    duration_ms=_duration_ms(started_at),
                    retry_count=attempt - 1,
                    input_reference=input_reference,
                    error_code=safe_error_code(exc),
                )
                raise InvocationFailedError(
                    component="agent",
                    name=definition.name,
                    attempts=attempt,
                ) from exc
            _log_completion(
                "posts.agent.succeeded",
                definition.name,
                invocation,
                attempt,
                started_at,
            )
            await self._record_trace(
                definition.name,
                invocation,
                status=ExecutionRunStatus.SUCCEEDED,
                started_at=started_wall,
                duration_ms=_duration_ms(started_at),
                retry_count=attempt - 1,
                input_reference=input_reference,
                output_reference=trace_reference(output),
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
                    kind=ExecutionRunKind.AGENT,
                    name=name,
                    **fields,
                )
            )
        except Exception:  # noqa: BLE001 - telemetry must not break agent execution
            logger.exception("posts.trace.record_failed", extra={"trace_kind": "agent"})


def _can_retry(policy: RetryPolicy, *, attempt: int, timeout: bool) -> bool:
    enabled = policy.retry_on_timeout if timeout else policy.retry_on_error
    return enabled and attempt < policy.max_attempts


async def _backoff(policy: RetryPolicy) -> None:
    if policy.backoff_seconds:
        await asyncio.sleep(policy.backoff_seconds)


def _log_retry(
    agent_name: str,
    invocation: InvocationContext,
    attempt: int,
    reason: str,
) -> None:
    logger.info(
        "posts.agent.retry",
        extra={
            **_agent_log_fields(
                "posts.agent.retry",
                agent_name,
                invocation,
                attempt=attempt,
            ),
            "retry_reason": reason,
        },
    )


def _log_completion(
    event: str,
    agent_name: str,
    invocation: InvocationContext,
    attempt: int,
    started_at: float,
) -> None:
    logger.info(
        event,
        extra={
            **_agent_log_fields(event, agent_name, invocation, attempt=attempt),
            "duration_ms": round((monotonic() - started_at) * 1000, 3),
        },
    )


def _duration_ms(started_at: float) -> int:
    return max(0, round((monotonic() - started_at) * 1000))


def _agent_log_fields(
    event: str,
    agent_name: str,
    invocation: InvocationContext,
    *,
    attempt: int,
) -> dict[str, Any]:
    return {
        "event": event,
        "agent_name": agent_name,
        "correlation_id": str(invocation.correlation_id),
        "post_id": str(invocation.post_id) if invocation.post_id else None,
        "generation_id": (str(invocation.generation_id) if invocation.generation_id else None),
        "attempt": attempt,
    }


__all__ = ["AgentExecutionContext", "AgentHandler", "AgentRuntime"]
