from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.posts.domain.enums import GenerationJobStatus


@dataclass(frozen=True, slots=True)
class GenerationJob:
    id: UUID
    generation_id: UUID
    status: GenerationJobStatus
    attempts: int
    max_attempts: int
    timeout_seconds: int
    available_at: datetime
    leased_until: datetime | None
    worker_id: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class GenerationExecutor(Protocol):
    """Ticket 11 plugs the Post Supervisor into this execution boundary."""

    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None: ...


class NonRetryableJobError(RuntimeError):
    """Signals a safe terminal failure without consuming further retries."""


__all__ = ["GenerationExecutor", "GenerationJob", "NonRetryableJobError"]
