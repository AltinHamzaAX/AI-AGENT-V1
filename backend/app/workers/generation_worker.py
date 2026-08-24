import asyncio
import logging
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories.generation_jobs import (
    SQLAlchemyGenerationJobRepository,
)
from app.modules.posts.domain.jobs import GenerationExecutor, NonRetryableJobError

logger = logging.getLogger(__name__)


class GenerationWorker:
    """Claims durable jobs and delegates execution to the Ticket 11 Supervisor boundary."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        executor: GenerationExecutor,
        lease_seconds: int,
        retry_backoff_seconds: int,
        poll_seconds: float,
        worker_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._retry_backoff_seconds = retry_backoff_seconds
        self._poll_seconds = poll_seconds
        self.worker_id = worker_id or f"worker-{uuid4().hex}"

    async def run_once(self) -> bool:
        async with self._session_factory.begin() as session:
            job = await SQLAlchemyGenerationJobRepository(session).claim_next(
                worker_id=self.worker_id,
                lease_seconds=self._lease_seconds,
            )
        if job is None:
            return False

        try:
            await asyncio.wait_for(
                self._executor.execute(generation_id=job.generation_id, job_id=job.id),
                timeout=job.timeout_seconds,
            )
        except asyncio.CancelledError:
            # The lease remains durable and another worker reclaims it after expiry.
            raise
        except TimeoutError:
            await self._record_failure(job_id=job.id, error_code="timeout", retryable=True)
        except NonRetryableJobError:
            await self._record_failure(
                job_id=job.id,
                error_code="non_retryable",
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary must contain task failures
            await self._record_failure(
                job_id=job.id,
                error_code=type(exc).__name__,
                retryable=True,
            )
        else:
            async with self._session_factory.begin() as session:
                await SQLAlchemyGenerationJobRepository(session).complete(
                    job_id=job.id,
                    worker_id=self.worker_id,
                )
        return True

    async def run_forever(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            processed = await self.run_once()
            if processed:
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def _record_failure(
        self,
        *,
        job_id: UUID,
        error_code: str,
        retryable: bool,
    ) -> None:
        async with self._session_factory.begin() as session:
            await SQLAlchemyGenerationJobRepository(session).fail(
                job_id=job_id,
                worker_id=self.worker_id,
                error_code=error_code,
                retryable=retryable,
                retry_delay_seconds=self._retry_backoff_seconds,
            )


__all__ = ["GenerationWorker"]
