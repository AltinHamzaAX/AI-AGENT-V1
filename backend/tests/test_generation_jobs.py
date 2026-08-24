import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.generation_jobs import (
    SQLAlchemyGenerationJobRepository,
)
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.models.posts import PostGenerationJobModel, PostGenerationModel
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.services import PostsService
from app.workers.generation_worker import GenerationWorker


@pytest_asyncio.fixture
async def job_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def job_client(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with job_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid4()), "X-Project-ID": str(uuid4())}


async def _queued_generation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_attempts: int = 3,
    timeout_seconds: int = 5,
) -> tuple[UUID, UUID]:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with session_factory.begin() as session:
        service = PostsService(
            SQLAlchemyPostRepository(session),
            generation_job_max_attempts=max_attempts,
            generation_job_timeout_seconds=timeout_seconds,
        )
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="Worker test",
        )
        generation = await service.request_generation(
            post_id=post.id,
            scope=scope,
            idempotency_key="worker-test",
        )
    return generation.id, generation.job_id


class SuccessfulExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None:
        self.calls.append((generation_id, job_id))


class RetryableExecutor:
    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None:
        raise RuntimeError("provider details must not be persisted")


class TerminalExecutor:
    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None:
        raise NonRetryableJobError("invalid persisted state")


class SlowExecutor:
    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None:
        await asyncio.sleep(2)


@pytest.mark.asyncio
async def test_generation_request_is_idempotent_and_exposes_job(
    job_client: AsyncClient,
) -> None:
    headers = _headers()
    post_response = await job_client.post(
        "/api/posts", headers=headers, json={"title": "Idempotency"}
    )
    post_id = post_response.json()["id"]
    request_headers = {**headers, "Idempotency-Key": "client-request-42"}

    first = await job_client.post(
        f"/api/posts/{post_id}/generations", headers=request_headers
    )
    second = await job_client.post(
        f"/api/posts/{post_id}/generations", headers=request_headers
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["job_id"] == first.json()["job_id"]
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    job = await job_client.get(
        f"/api/posts/{post_id}/generations/{first.json()['id']}/job",
        headers=headers,
    )
    assert job.status_code == 200
    assert job.json()["status"] == "queued"
    assert job.json()["attempts"] == 0


@pytest.mark.asyncio
async def test_worker_completes_job_once(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    generation_id, job_id = await _queued_generation(job_session_factory)
    executor = SuccessfulExecutor()
    worker = GenerationWorker(
        session_factory=job_session_factory,
        executor=executor,
        lease_seconds=30,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-success",
    )

    assert await worker.run_once() is True
    assert executor.calls == [(generation_id, job_id)]
    assert await worker.run_once() is False
    async with job_session_factory() as session:
        job = await session.get(PostGenerationJobModel, job_id)
        generation = await session.get(PostGenerationModel, generation_id)
        assert job is not None and job.status == "completed" and job.attempts == 1
        assert generation is not None and generation.status == "completed"


@pytest.mark.asyncio
async def test_retryable_failure_retries_then_dead_letters(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    generation_id, job_id = await _queued_generation(job_session_factory, max_attempts=3)
    worker = GenerationWorker(
        session_factory=job_session_factory,
        executor=RetryableExecutor(),
        lease_seconds=30,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-retry",
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    async with job_session_factory() as session:
        job = await session.get(PostGenerationJobModel, job_id)
        generation = await session.get(PostGenerationModel, generation_id)
        assert job is not None and job.status == "dead" and job.attempts == 3
        assert job.last_error_code == "RuntimeError"
        assert generation is not None and generation.status == "failed"


@pytest.mark.asyncio
async def test_non_retryable_failure_is_terminal_immediately(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    generation_id, job_id = await _queued_generation(job_session_factory)
    worker = GenerationWorker(
        session_factory=job_session_factory,
        executor=TerminalExecutor(),
        lease_seconds=30,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-terminal",
    )

    assert await worker.run_once() is True
    async with job_session_factory() as session:
        job = await session.get(PostGenerationJobModel, job_id)
        generation = await session.get(PostGenerationModel, generation_id)
        assert job is not None and job.status == "failed" and job.attempts == 1
        assert job.last_error_code == "non_retryable"
        assert generation is not None and generation.status == "failed"


@pytest.mark.asyncio
async def test_job_timeout_is_recorded_and_scheduled_for_retry(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, job_id = await _queued_generation(job_session_factory, timeout_seconds=1)
    worker = GenerationWorker(
        session_factory=job_session_factory,
        executor=SlowExecutor(),
        lease_seconds=2,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-timeout",
    )

    assert await worker.run_once() is True
    async with job_session_factory() as session:
        job = await session.get(PostGenerationJobModel, job_id)
        assert job is not None and job.status == "retry_scheduled"
        assert job.last_error_code == "timeout"


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_after_worker_restart(
    job_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    generation_id, job_id = await _queued_generation(job_session_factory)
    async with job_session_factory.begin() as session:
        claimed = await SQLAlchemyGenerationJobRepository(session).claim_next(
            worker_id="worker-before-restart",
            lease_seconds=30,
        )
        assert claimed is not None and claimed.attempts == 1
    async with job_session_factory.begin() as session:
        job = await session.get(PostGenerationJobModel, job_id)
        assert job is not None
        job.leased_until = datetime.now(UTC) - timedelta(seconds=1)

    executor = SuccessfulExecutor()
    restarted_worker = GenerationWorker(
        session_factory=job_session_factory,
        executor=executor,
        lease_seconds=30,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-after-restart",
    )
    assert await restarted_worker.run_once() is True
    assert executor.calls == [(generation_id, job_id)]
    async with job_session_factory() as session:
        job = (
            await session.execute(
                select(PostGenerationJobModel).where(PostGenerationJobModel.id == job_id)
            )
        ).scalar_one()
        assert job.status == "completed"
        assert job.attempts == 2
