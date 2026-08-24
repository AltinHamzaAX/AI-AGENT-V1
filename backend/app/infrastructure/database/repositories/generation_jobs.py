from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posts import PostGenerationJobModel, PostGenerationModel
from app.modules.posts.domain.enums import GenerationJobStatus, GenerationStatus
from app.modules.posts.domain.jobs import GenerationJob


def _job(model: PostGenerationJobModel) -> GenerationJob:
    return GenerationJob(
        id=model.id,
        generation_id=model.generation_id,
        status=GenerationJobStatus(model.status),
        attempts=model.attempts,
        max_attempts=model.max_attempts,
        timeout_seconds=model.timeout_seconds,
        available_at=model.available_at,
        leased_until=model.leased_until,
        worker_id=model.worker_id,
        last_error_code=model.last_error_code,
        created_at=model.created_at,
        updated_at=model.updated_at,
        completed_at=model.completed_at,
    )


class SQLAlchemyGenerationJobRepository:
    """PostgreSQL-backed queue with leases; Redis is not the source of truth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> GenerationJob | None:
        now = datetime.now(UTC)
        await self._dead_letter_exhausted_leases(now=now)
        claimable = or_(
            and_(
                PostGenerationJobModel.status.in_(
                    (
                        GenerationJobStatus.QUEUED.value,
                        GenerationJobStatus.RETRY_SCHEDULED.value,
                    )
                ),
                PostGenerationJobModel.available_at <= now,
            ),
            and_(
                PostGenerationJobModel.status == GenerationJobStatus.RUNNING.value,
                PostGenerationJobModel.leased_until <= now,
            ),
        )
        statement = (
            select(PostGenerationJobModel)
            .where(claimable, PostGenerationJobModel.attempts < PostGenerationJobModel.max_attempts)
            .order_by(PostGenerationJobModel.available_at, PostGenerationJobModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        model = (await self._session.execute(statement)).scalar_one_or_none()
        if model is None:
            return None
        return await self._mark_claimed(
            model=model,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    async def claim_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> GenerationJob | None:
        """Claims a known job; useful for queue notifications and deterministic tests."""
        now = datetime.now(UTC)
        await self._dead_letter_exhausted_leases(now=now)
        claimable = or_(
            and_(
                PostGenerationJobModel.status.in_(
                    (
                        GenerationJobStatus.QUEUED.value,
                        GenerationJobStatus.RETRY_SCHEDULED.value,
                    )
                ),
                PostGenerationJobModel.available_at <= now,
            ),
            and_(
                PostGenerationJobModel.status == GenerationJobStatus.RUNNING.value,
                PostGenerationJobModel.leased_until <= now,
            ),
        )
        statement = (
            select(PostGenerationJobModel)
            .where(
                PostGenerationJobModel.id == job_id,
                claimable,
                PostGenerationJobModel.attempts < PostGenerationJobModel.max_attempts,
            )
            .with_for_update(skip_locked=True)
        )
        model = (await self._session.execute(statement)).scalar_one_or_none()
        if model is None:
            return None
        return await self._mark_claimed(
            model=model,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    async def complete(self, *, job_id: UUID, worker_id: str) -> GenerationJob | None:
        model = await self._owned_running_job(job_id=job_id, worker_id=worker_id)
        if model is None:
            return None
        now = datetime.now(UTC)
        model.status = GenerationJobStatus.COMPLETED.value
        model.leased_until = None
        model.worker_id = None
        model.completed_at = now
        model.updated_at = now
        generation = await self._session.get(PostGenerationModel, model.generation_id)
        if generation is not None:
            generation.status = GenerationStatus.COMPLETED.value
            generation.updated_at = now
        await self._session.flush()
        await self._session.refresh(model)
        return _job(model)

    async def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_code: str,
        retryable: bool,
        retry_delay_seconds: int,
    ) -> GenerationJob | None:
        model = await self._owned_running_job(job_id=job_id, worker_id=worker_id)
        if model is None:
            return None
        now = datetime.now(UTC)
        model.last_error_code = error_code[:200]
        model.leased_until = None
        model.worker_id = None
        model.updated_at = now
        generation = await self._session.get(PostGenerationModel, model.generation_id)
        if retryable and model.attempts < model.max_attempts:
            model.status = GenerationJobStatus.RETRY_SCHEDULED.value
            model.available_at = now + timedelta(seconds=retry_delay_seconds)
            if generation is not None:
                generation.status = GenerationStatus.QUEUED.value
                generation.updated_at = now
        else:
            model.status = (
                GenerationJobStatus.DEAD.value
                if retryable
                else GenerationJobStatus.FAILED.value
            )
            if generation is not None:
                generation.status = GenerationStatus.FAILED.value
                generation.updated_at = now
        await self._session.flush()
        await self._session.refresh(model)
        return _job(model)

    async def _owned_running_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
    ) -> PostGenerationJobModel | None:
        statement = (
            select(PostGenerationJobModel)
            .where(
                PostGenerationJobModel.id == job_id,
                PostGenerationJobModel.status == GenerationJobStatus.RUNNING.value,
                PostGenerationJobModel.worker_id == worker_id[:200],
            )
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def _mark_claimed(
        self,
        *,
        model: PostGenerationJobModel,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> GenerationJob:
        model.status = GenerationJobStatus.RUNNING.value
        model.attempts += 1
        model.worker_id = worker_id[:200]
        model.leased_until = now + timedelta(seconds=lease_seconds)
        model.updated_at = now
        generation = await self._session.get(PostGenerationModel, model.generation_id)
        if generation is not None:
            generation.status = GenerationStatus.RUNNING.value
            generation.updated_at = now
        await self._session.flush()
        await self._session.refresh(model)
        return _job(model)

    async def _dead_letter_exhausted_leases(self, *, now: datetime) -> None:
        statement = select(PostGenerationJobModel).where(
            PostGenerationJobModel.status == GenerationJobStatus.RUNNING.value,
            PostGenerationJobModel.leased_until <= now,
            PostGenerationJobModel.attempts >= PostGenerationJobModel.max_attempts,
        )
        models = (await self._session.execute(statement)).scalars().all()
        for model in models:
            model.status = GenerationJobStatus.DEAD.value
            model.worker_id = None
            model.leased_until = None
            model.last_error_code = model.last_error_code or "lease_expired"
            model.updated_at = now
            generation = await self._session.get(PostGenerationModel, model.generation_id)
            if generation is not None:
                generation.status = GenerationStatus.FAILED.value
                generation.updated_at = now
        if models:
            await self._session.flush()
