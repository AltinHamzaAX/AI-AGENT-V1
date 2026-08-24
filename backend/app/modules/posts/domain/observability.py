import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel


class ExecutionRunKind(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    PROVIDER = "provider"
    GENERATION_STEP = "generation_step"


class ExecutionRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    id: UUID
    generation_id: UUID
    correlation_id: UUID
    kind: ExecutionRunKind
    name: str
    status: ExecutionRunStatus
    input_reference: str | None
    output_reference: str | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    duration_ms: int
    retry_count: int
    error_code: str | None
    metadata: dict[str, Any]
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionTraceCreate:
    generation_id: UUID
    correlation_id: UUID
    kind: ExecutionRunKind
    name: str
    status: ExecutionRunStatus
    duration_ms: int
    retry_count: int = 0
    input_reference: str | None = None
    output_reference: str | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    error_code: str | None = None
    metadata: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 200:
            raise ValueError("trace name must contain 1 to 200 characters")
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        if self.retry_count < 0:
            raise ValueError("retry_count cannot be negative")
        for value, label in (
            (self.input_tokens, "input_tokens"),
            (self.output_tokens, "output_tokens"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} cannot be negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd cannot be negative")


class ExecutionTraceRecorder(Protocol):
    async def record(self, trace: ExecutionTraceCreate) -> None: ...


class InMemoryExecutionTraceRecorder:
    """Deterministic recorder for tests and local orchestration composition."""

    def __init__(self) -> None:
        self.traces: list[ExecutionTraceCreate] = []

    async def record(self, trace: ExecutionTraceCreate) -> None:
        self.traces.append(trace)


def trace_reference(value: Any) -> str:
    """Return a non-reversible reference; raw inputs and outputs are never persisted."""

    if isinstance(value, bytes):
        payload = value
    else:
        normalized = _trace_value(value)
        payload = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def safe_error_code(exc: BaseException) -> str:
    return type(exc).__name__[:200]


def completed_trace(
    *,
    generation_id: UUID,
    correlation_id: UUID,
    kind: ExecutionRunKind,
    name: str,
    status: ExecutionRunStatus,
    started_at: datetime,
    duration_ms: int,
    **kwargs: Any,
) -> ExecutionTraceCreate:
    return ExecutionTraceCreate(
        generation_id=generation_id,
        correlation_id=correlation_id,
        kind=kind,
        name=name,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        duration_ms=duration_ms,
        **kwargs,
    )


def _trace_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return value


def trace_id() -> UUID:
    return uuid4()


__all__ = [
    "ExecutionRunKind",
    "ExecutionRunStatus",
    "ExecutionTrace",
    "ExecutionTraceCreate",
    "ExecutionTraceRecorder",
    "InMemoryExecutionTraceRecorder",
    "completed_trace",
    "safe_error_code",
    "trace_reference",
    "trace_id",
]
