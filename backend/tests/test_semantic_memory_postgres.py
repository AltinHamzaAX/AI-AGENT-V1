import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.post_memory_scope import (
    SQLAlchemyPostMemoryScopeResolver,
)
from app.infrastructure.database.repositories.semantic_memory import (
    SQLAlchemySemanticMemoryRepository,
)
from app.integrations.mock import MockEmbeddingProvider
from app.models.posts import PostModel, PostSemanticMemoryModel
from app.modules.posts.domain.memory import (
    SemanticMemoryKind,
    SemanticMemoryScope,
    SemanticMemoryScopeLevel,
)
from app.modules.posts.services import SemanticMemoryService

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


@pytest.mark.asyncio
async def test_pgvector_retrieval_is_exactly_user_and_brand_scoped(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_a = uuid4()
    user_b = uuid4()
    brand_a_id = uuid4()
    scope_a = SemanticMemoryScope(
        user_id=user_a,
        level=SemanticMemoryScopeLevel.BRAND,
        brand_id=brand_a_id,
    )
    scope_b = SemanticMemoryScope(
        user_id=user_a,
        level=SemanticMemoryScopeLevel.BRAND,
        brand_id=uuid4(),
    )
    other_user_scope = SemanticMemoryScope(
        user_id=user_b,
        level=SemanticMemoryScopeLevel.BRAND,
        brand_id=brand_a_id,
    )

    try:
        async with postgres_session_factory.begin() as session:
            service = SemanticMemoryService(
                SQLAlchemySemanticMemoryRepository(session),
                MockEmbeddingProvider(dimension=768),
            )
            expected = await service.store(
                scope=scope_a,
                kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
                content="Use tactile product details to communicate craftsmanship",
            )
            duplicate = await service.store(
                scope=scope_a,
                kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
                content="Use tactile product details to communicate craftsmanship",
            )
            assert duplicate.id == expected.id
            await service.store(
                scope=scope_b,
                kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
                content="Private concept from another brand",
            )
            await service.store(
                scope=other_user_scope,
                kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
                content="Private concept from another user",
            )

        async with postgres_session_factory() as session:
            service = SemanticMemoryService(
                SQLAlchemySemanticMemoryRepository(session),
                MockEmbeddingProvider(dimension=768),
            )
            matches = await service.retrieve(
                scope=scope_a,
                query="How should craftsmanship be communicated?",
            )

        assert [match.memory.id for match in matches] == [expected.id]
        assert all(match.memory.scope == scope_a for match in matches)
    finally:
        async with postgres_session_factory.begin() as session:
            await session.execute(
                delete(PostSemanticMemoryModel).where(
                    PostSemanticMemoryModel.user_id.in_((user_a, user_b))
                )
            )


@pytest.mark.asyncio
async def test_post_memory_scope_resolves_the_owning_user_and_project(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    post_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    try:
        async with postgres_session_factory.begin() as session:
            session.add(PostModel(id=post_id, user_id=user_id, project_id=project_id))

        async with postgres_session_factory() as session:
            scope = await SQLAlchemyPostMemoryScopeResolver(session).resolve_project_scope(
                post_id=post_id
            )

        assert scope == SemanticMemoryScope(
            user_id=user_id,
            level=SemanticMemoryScopeLevel.PROJECT,
            project_id=project_id,
        )
    finally:
        async with postgres_session_factory.begin() as session:
            await session.execute(delete(PostModel).where(PostModel.id == post_id))
