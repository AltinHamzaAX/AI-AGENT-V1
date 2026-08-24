import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.models.posts import (
    GenerationArtifactModel,
    PostGenerationModel,
    PostGenerationStateModel,
    PostGenerationStateVersionModel,
    PostModel,
)
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import GenerationArtifactKind, PostWorkflowSection
from app.modules.posts.domain.exceptions import WorkflowStateConflictError
from app.modules.posts.services import PostsService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _delete_post(
    session_factory: async_sessionmaker[AsyncSession],
    post_id: UUID,
) -> None:
    async with session_factory.begin() as session:
        await session.execute(delete(PostModel).where(PostModel.id == post_id))


@pytest.mark.asyncio
async def test_postgres_concurrent_generation_attempts_are_unique_and_ordered(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=uuid4(),
            title="Concurrent attempts",
        )

    async def request_attempt() -> int:
        async with postgres_session_factory.begin() as session:
            service = PostsService(SQLAlchemyPostRepository(session))
            generation = await service.request_generation(post_id=post.id, scope=scope)
            return generation.attempt

    try:
        attempts = await asyncio.gather(*(request_attempt() for _ in range(20)))
        assert sorted(attempts) == list(range(1, 21))

        async with postgres_session_factory() as session:
            service = PostsService(SQLAlchemyPostRepository(session))
            generations = await service.list_generations(post_id=post.id, scope=scope)
            assert [generation.attempt for generation in generations] == list(range(1, 21))
            assert all(generation.status.value == "pending" for generation in generations)
    finally:
        await _delete_post(postgres_session_factory, post.id)


@pytest.mark.asyncio
async def test_postgres_constraints_and_post_cascade_cover_generations_and_artifacts(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="Constraint and cascade",
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)
        artifact = await service.add_artifact(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
            kind=GenerationArtifactKind.FINAL,
            storage_key=f"generations/{generation.id}/final.png",
            mime_type="image/png",
            size_bytes=2048,
            checksum="b" * 64,
            width=1080,
            height=1350,
        )

    invalid_generations = [
        PostGenerationModel(post_id=post.id, attempt=0, status="pending"),
        PostGenerationModel(post_id=post.id, attempt=2, status="invalid"),
        PostGenerationModel(post_id=post.id, attempt=1, status="queued"),
    ]
    for invalid in invalid_generations:
        with pytest.raises(IntegrityError):
            async with postgres_session_factory.begin() as session:
                session.add(invalid)

    invalid_artifacts = [
        GenerationArtifactModel(
            generation_id=generation.id,
            kind="invalid",
            storage_key=f"invalid/{uuid4()}",
            mime_type="image/png",
            size_bytes=1,
            checksum="c" * 64,
            artifact_metadata={},
        ),
        GenerationArtifactModel(
            generation_id=generation.id,
            kind="preview",
            storage_key=f"invalid/{uuid4()}",
            mime_type="image/png",
            size_bytes=0,
            checksum="c" * 64,
            artifact_metadata={},
        ),
        GenerationArtifactModel(
            generation_id=generation.id,
            kind="preview",
            storage_key=f"invalid/{uuid4()}",
            mime_type="image/png",
            size_bytes=1,
            checksum="short",
            artifact_metadata={},
        ),
        GenerationArtifactModel(
            generation_id=generation.id,
            kind="preview",
            storage_key=f"invalid/{uuid4()}",
            mime_type="image/png",
            size_bytes=1,
            checksum="c" * 64,
            width=100,
            height=None,
            artifact_metadata={},
        ),
    ]
    for invalid in invalid_artifacts:
        with pytest.raises(IntegrityError):
            async with postgres_session_factory.begin() as session:
                session.add(invalid)

    await _delete_post(postgres_session_factory, post.id)
    async with postgres_session_factory() as session:
        generation_count = await session.scalar(
            select(func.count(PostGenerationModel.id)).where(
                PostGenerationModel.id == generation.id
            )
        )
        artifact_count = await session.scalar(
            select(func.count(GenerationArtifactModel.id)).where(
                GenerationArtifactModel.id == artifact.id
            )
        )
        state_count = await session.scalar(
            select(func.count(PostGenerationStateModel.generation_id)).where(
                PostGenerationStateModel.generation_id == generation.id
            )
        )
        state_version_count = await session.scalar(
            select(func.count(PostGenerationStateVersionModel.generation_id)).where(
                PostGenerationStateVersionModel.generation_id == generation.id
            )
        )
    assert generation_count == 0
    assert artifact_count == 0
    assert state_count == 0
    assert state_version_count == 0


@pytest.mark.asyncio
async def test_postgres_state_survives_sessions_and_optimistic_concurrency(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="Restart-safe state",
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)

    async with postgres_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        recovered = await service.get_workflow_state(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
        )
        assert recovered.version == 1
        assert recovered.data["brief"] == {}

    async def write_brief(goal: str) -> str:
        try:
            async with postgres_session_factory.begin() as session:
                service = PostsService(SQLAlchemyPostRepository(session))
                await service.write_workflow_section(
                    generation_id=generation.id,
                    post_id=post.id,
                    scope=scope,
                    section=PostWorkflowSection.BRIEF,
                    value={"goal": goal},
                    expected_version=1,
                )
            return "written"
        except WorkflowStateConflictError:
            return "conflict"

    try:
        outcomes = await asyncio.gather(write_brief("A"), write_brief("B"))
        assert sorted(outcomes) == ["conflict", "written"]

        async with postgres_session_factory() as session:
            service = PostsService(SQLAlchemyPostRepository(session))
            recovered = await service.get_workflow_state(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
            )
            history = await service.list_workflow_state_versions(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
            )
            assert recovered.version == 2
            assert recovered.data["brief"]["goal"] in {"A", "B"}
            assert [snapshot.version for snapshot in history] == [1, 2]
            assert history[0].data["brief"] == {}
    finally:
        await _delete_post(postgres_session_factory, post.id)
