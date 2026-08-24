import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.models.conversations import ConversationModel, MessageModel
from app.shared.conversations.domain import ConversationScope, MessageRole

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


async def _delete_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: UUID,
) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            delete(ConversationModel).where(ConversationModel.id == conversation_id)
        )


@pytest.mark.asyncio
async def test_postgres_concurrent_appends_are_unique_and_chronological(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        repository = SQLAlchemyConversationRepository(session)
        conversation = await repository.create(scope=scope, title="Concurrency test")

    async def append(index: int) -> None:
        async with postgres_session_factory.begin() as session:
            repository = SQLAlchemyConversationRepository(session)
            message = await repository.append_message(
                conversation_id=conversation.id,
                scope=scope,
                role=MessageRole.USER,
                content=f"Mesazhi {index}: është Unicode ✓",
                metadata={"index": index, "nested": {"valid": True}},
            )
            assert message is not None

    try:
        await asyncio.gather(*(append(index) for index in range(20)))
        async with postgres_session_factory() as session:
            repository = SQLAlchemyConversationRepository(session)
            history = await repository.history(
                conversation_id=conversation.id,
                scope=scope,
                offset=0,
                limit=100,
            )
            assert history is not None
            assert history.total == 20
            assert [message.sequence for message in history.items] == list(range(1, 21))
            assert {message.metadata["index"] for message in history.items} == set(range(20))
            assert all("është Unicode ✓" in message.content for message in history.items)

            wrong_scope = await repository.history(
                conversation_id=conversation.id,
                scope=ConversationScope(user_id=uuid4(), project_id=scope.project_id),
                offset=0,
                limit=100,
            )
            assert wrong_scope is None
    finally:
        await _delete_conversation(postgres_session_factory, conversation.id)


@pytest.mark.asyncio
async def test_postgres_constraints_reject_invalid_and_duplicate_messages(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        repository = SQLAlchemyConversationRepository(session)
        conversation = await repository.create(scope=scope, title="Constraint test")

    try:
        invalid_models = [
            MessageModel(
                conversation_id=conversation.id,
                sequence=1,
                role="invalid",
                content="valid",
                message_metadata={},
            ),
            MessageModel(
                conversation_id=conversation.id,
                sequence=0,
                role="user",
                content="valid",
                message_metadata={},
            ),
            MessageModel(
                conversation_id=conversation.id,
                sequence=1,
                role="user",
                content="",
                message_metadata={},
            ),
        ]
        for model in invalid_models:
            with pytest.raises(IntegrityError):
                async with postgres_session_factory.begin() as session:
                    session.add(model)

        async with postgres_session_factory.begin() as session:
            session.add(
                MessageModel(
                    conversation_id=conversation.id,
                    sequence=1,
                    role="user",
                    content="first",
                    message_metadata={},
                )
            )
        with pytest.raises(IntegrityError):
            async with postgres_session_factory.begin() as session:
                session.add(
                    MessageModel(
                        conversation_id=conversation.id,
                        sequence=1,
                        role="assistant",
                        content="duplicate sequence",
                        message_metadata={},
                    )
                )
    finally:
        await _delete_conversation(postgres_session_factory, conversation.id)


@pytest.mark.asyncio
async def test_postgres_rolls_back_and_cascades_message_deletion(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rolled_back_id = None
    with pytest.raises(RuntimeError, match="force rollback"):
        async with postgres_session_factory.begin() as session:
            repository = SQLAlchemyConversationRepository(session)
            rolled_back = await repository.create(
                scope=ConversationScope(user_id=uuid4(), project_id=uuid4()),
                title="Rollback test",
            )
            rolled_back_id = rolled_back.id
            raise RuntimeError("force rollback")

    assert rolled_back_id is not None
    async with postgres_session_factory() as session:
        rolled_back_count = await session.scalar(
            select(func.count(ConversationModel.id)).where(ConversationModel.id == rolled_back_id)
        )
    assert rolled_back_count == 0

    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    async with postgres_session_factory.begin() as session:
        repository = SQLAlchemyConversationRepository(session)
        conversation = await repository.create(scope=scope, title="Cascade test")
        message = await repository.append_message(
            conversation_id=conversation.id,
            scope=scope,
            role=MessageRole.USER,
            content="Delete with parent",
            metadata={},
        )
        assert message is not None

    await _delete_conversation(postgres_session_factory, conversation.id)
    async with postgres_session_factory() as session:
        message_count = await session.scalar(
            select(func.count(MessageModel.id)).where(
                MessageModel.conversation_id == conversation.id
            )
        )
    assert message_count == 0
