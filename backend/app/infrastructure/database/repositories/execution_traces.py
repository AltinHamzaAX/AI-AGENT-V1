from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.posts import PostExecutionTraceModel
from app.modules.posts.domain.observability import ExecutionTraceCreate


class SQLAlchemyExecutionTraceRecorder:
    """Persists each completed trace in an independent transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, trace: ExecutionTraceCreate) -> None:
        completed_at = trace.completed_at or datetime.now(UTC)
        started_at = trace.started_at or completed_at - timedelta(milliseconds=trace.duration_ms)
        async with self._session_factory.begin() as session:
            session.add(
                PostExecutionTraceModel(
                    generation_id=trace.generation_id,
                    correlation_id=trace.correlation_id,
                    kind=trace.kind.value,
                    name=trace.name,
                    status=trace.status.value,
                    input_reference=trace.input_reference,
                    output_reference=trace.output_reference,
                    provider=trace.provider,
                    model=trace.model,
                    input_tokens=trace.input_tokens,
                    output_tokens=trace.output_tokens,
                    cost_usd=trace.cost_usd,
                    duration_ms=trace.duration_ms,
                    retry_count=trace.retry_count,
                    error_code=trace.error_code,
                    trace_metadata=dict(trace.metadata or {}),
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )


__all__ = ["SQLAlchemyExecutionTraceRecorder"]
