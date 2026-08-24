import os
from collections.abc import AsyncIterator
from io import BytesIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.assets import SQLAlchemyAssetRepository
from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.models.assets import AssetModel
from app.models.conversations import ConversationModel
from app.shared.assets.domain import AssetRole
from app.shared.assets.service import AssetService
from app.shared.conversations.domain import ConversationScope, MessageRole

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def is_available(self) -> bool:
        return True

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.objects[key] = data

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 36), color=(40, 80, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _delete_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    conversation_id: UUID,
) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            delete(ConversationModel).where(ConversationModel.id == conversation_id)
        )


@pytest.mark.asyncio
async def test_postgres_asset_repository_scoping_deduplication_and_cascade(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = ConversationScope(user_id=uuid4(), project_id=uuid4())
    storage = MemoryStorage()
    async with postgres_session_factory.begin() as session:
        conversations = SQLAlchemyConversationRepository(session)
        conversation = await conversations.create(scope=scope, title="Asset persistence")
        first_message = await conversations.append_message(
            conversation_id=conversation.id,
            scope=scope,
            role=MessageRole.USER,
            content="First asset",
            metadata={},
        )
        second_message = await conversations.append_message(
            conversation_id=conversation.id,
            scope=scope,
            role=MessageRole.USER,
            content="Reuse the asset",
            metadata={},
        )
        assert first_message is not None
        assert second_message is not None

    try:
        async with postgres_session_factory.begin() as session:
            service = AssetService(
                repository=SQLAlchemyAssetRepository(session),
                storage=storage,
                max_size_bytes=1_000_000,
                max_dimension=1_000,
                max_pixels=1_000_000,
            )
            first = await service.upload(
                scope=scope,
                message_id=first_message.id,
                role=AssetRole.LOGO,
                original_filename="logo.png",
                declared_mime_type="image/png",
                data=_png(),
            )
            reused = await service.upload(
                scope=scope,
                message_id=second_message.id,
                role=AssetRole.SUPPORTING_ASSET,
                original_filename="same.png",
                declared_mime_type="image/png",
                data=_png(),
            )
            assert first.deduplicated is False
            assert reused.deduplicated is True
            assert first.asset.storage_key == reused.asset.storage_key

        async with postgres_session_factory() as session:
            repository = SQLAlchemyAssetRepository(session)
            assert await repository.get(asset_id=first.asset.id, scope=scope) is not None
            assert (
                await repository.get(
                    asset_id=first.asset.id,
                    scope=ConversationScope(user_id=uuid4(), project_id=scope.project_id),
                )
                is None
            )
            rows = await repository.list_for_message(message_id=first_message.id, scope=scope)
            assert rows is not None
            assert [asset.id for asset in rows] == [first.asset.id]
            assert len(storage.objects) == 1

        await _delete_conversation(postgres_session_factory, conversation.id)
        async with postgres_session_factory() as session:
            count = await session.scalar(
                select(func.count(AssetModel.id)).where(
                    AssetModel.id.in_([first.asset.id, reused.asset.id])
                )
            )
        assert count == 0
    finally:
        await _delete_conversation(postgres_session_factory, conversation.id)
