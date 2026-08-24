import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.base import SQLAlchemyRepository
from app.infrastructure.database.unit_of_work import SQLAlchemyUnitOfWork
from app.models.conversations import ConversationModel

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
async def test_postgres_connection_and_pgvector_extension(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_session_factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1
        extension_version = await session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    assert extension_version is not None


@pytest.mark.asyncio
async def test_postgres_unit_of_work_and_generic_repository_crud(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    repository_type = SQLAlchemyRepository[ConversationModel, UUID]

    try:
        async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
            repository = repository_type(unit_of_work.session, ConversationModel)
            await repository.add(
                ConversationModel(
                    id=conversation_id,
                    user_id=user_id,
                    project_id=project_id,
                    title="Repository create",
                )
            )
            await unit_of_work.commit()

        async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
            repository = repository_type(unit_of_work.session, ConversationModel)
            conversation = await repository.get(conversation_id)
            assert conversation is not None
            assert conversation.title == "Repository create"
            conversation.title = "Repository update"
            await repository.update(conversation)
            await unit_of_work.commit()

        async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
            repository = repository_type(unit_of_work.session, ConversationModel)
            updated = await repository.get(conversation_id)
            assert updated is not None
            assert updated.title == "Repository update"
            assert await repository.delete(conversation_id) is True
            await unit_of_work.commit()

        async with postgres_session_factory() as session:
            assert await session.get(ConversationModel, conversation_id) is None
    finally:
        await _delete_conversations(postgres_session_factory, conversation_id)


@pytest.mark.asyncio
async def test_postgres_unit_of_work_rollback_paths(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    explicit_rollback_id = uuid4()
    implicit_rollback_id = uuid4()
    exception_rollback_id = uuid4()
    repository_type = SQLAlchemyRepository[ConversationModel, UUID]

    try:
        async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
            repository = repository_type(unit_of_work.session, ConversationModel)
            await repository.add(_conversation(explicit_rollback_id))
            await unit_of_work.rollback()

        async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
            repository = repository_type(unit_of_work.session, ConversationModel)
            await repository.add(_conversation(implicit_rollback_id))

        with pytest.raises(RuntimeError, match="force rollback"):
            async with SQLAlchemyUnitOfWork(postgres_session_factory) as unit_of_work:
                repository = repository_type(unit_of_work.session, ConversationModel)
                await repository.add(_conversation(exception_rollback_id))
                raise RuntimeError("force rollback")

        async with postgres_session_factory() as session:
            existing_ids = set(
                (
                    await session.execute(
                        select(ConversationModel.id).where(
                            ConversationModel.id.in_(
                                [
                                    explicit_rollback_id,
                                    implicit_rollback_id,
                                    exception_rollback_id,
                                ]
                            )
                        )
                    )
                ).scalars()
            )
        assert existing_ids == set()
    finally:
        await _delete_conversations(
            postgres_session_factory,
            explicit_rollback_id,
            implicit_rollback_id,
            exception_rollback_id,
        )


def _conversation(identifier: UUID) -> ConversationModel:
    return ConversationModel(
        id=identifier,
        user_id=uuid4(),
        project_id=uuid4(),
        title="Rollback probe",
    )


async def _delete_conversations(
    session_factory: async_sessionmaker[AsyncSession],
    *conversation_ids: UUID,
) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            delete(ConversationModel).where(ConversationModel.id.in_(conversation_ids))
        )
