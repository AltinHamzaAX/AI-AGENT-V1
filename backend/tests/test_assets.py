from collections.abc import AsyncIterator
from io import BytesIO
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

from app.api.routes.assets import _read_upload
from app.dependencies.assets import get_asset_storage
from app.infrastructure.database.base import Base
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.modules.posts.providers import StorageObjectNotFoundError
from app.shared.assets.contracts import AssetRepository
from app.shared.assets.domain import AssetRole, AssetValidationError
from app.shared.assets.service import AssetService
from app.shared.assets.validation import validate_image_upload
from app.shared.conversations.domain import ConversationScope


class FakeAssetStorage:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}
        self.put_calls = 0
        self.fail_upload = fail_upload

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
        self.put_calls += 1
        if self.fail_upload:
            raise RuntimeError("storage unavailable")
        self.objects[key] = (data, content_type, metadata or {})

    async def get(self, *, key: str) -> bytes:
        stored = self.objects.get(key)
        if stored is None:
            raise StorageObjectNotFoundError(f"object '{key}' does not exist")
        return stored[0]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


@pytest_asyncio.fixture
async def asset_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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
async def asset_api(
    asset_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncClient, FakeAssetStorage]]:
    storage = FakeAssetStorage()

    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with asset_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    app.dependency_overrides[get_asset_storage] = lambda: storage
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, storage
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid4()), "X-Project-ID": str(uuid4())}


def _image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (32, 24),
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(20, 100, 180)).save(buffer, format=image_format)
    return buffer.getvalue()


async def _message(client: AsyncClient, headers: dict[str, str]) -> str:
    conversation = await client.post("/api/conversations", headers=headers, json={})
    assert conversation.status_code == 201
    message = await client.post(
        f"/api/conversations/{conversation.json()['id']}/messages",
        headers=headers,
        json={"content": "Use this uploaded asset"},
    )
    assert message.status_code == 201
    return str(message.json()["id"])


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    message_id: str,
    *,
    content: bytes | None = None,
    filename: str = "logo.png",
    mime_type: str = "image/png",
    role: str = "logo",
):
    return await client.post(
        "/api/assets",
        headers=headers,
        data={"message_id": message_id, "role": role},
        files={"file": (filename, content if content is not None else _image_bytes(), mime_type)},
    )


@pytest.mark.asyncio
async def test_upload_get_and_list_asset_with_verified_metadata(
    asset_api: tuple[AsyncClient, FakeAssetStorage],
) -> None:
    client, storage = asset_api
    headers = _headers()
    message_id = await _message(client, headers)

    response = await _upload(
        client,
        headers,
        message_id,
        filename="../brand/logo.png",
    )
    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["message_id"] == message_id
    assert uploaded["role"] == "logo"
    assert uploaded["original_filename"] == "logo.png"
    assert uploaded["mime_type"] == "image/png"
    assert uploaded["width"] == 32
    assert uploaded["height"] == 24
    assert uploaded["metadata"] == {"detected_format": "png"}
    assert uploaded["deduplicated"] is False
    assert len(uploaded["checksum"]) == 64
    assert len(storage.objects) == 1

    retrieved = await client.get(f"/api/assets/{uploaded['id']}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json() == {
        key: value for key, value in uploaded.items() if key != "deduplicated"
    }

    listed = await client.get(
        "/api/assets",
        headers=headers,
        params={"message_id": message_id},
    )
    assert listed.status_code == 200
    assert listed.json() == [retrieved.json()]


@pytest.mark.asyncio
async def test_duplicate_uploads_are_idempotent_and_reuse_the_stored_object(
    asset_api: tuple[AsyncClient, FakeAssetStorage],
) -> None:
    client, storage = asset_api
    headers = _headers()
    first_message_id = await _message(client, headers)
    second_message_id = await _message(client, headers)
    image = _image_bytes()

    first = await _upload(client, headers, first_message_id, content=image)
    same_message = await _upload(client, headers, first_message_id, content=image)
    other_message = await _upload(
        client,
        headers,
        second_message_id,
        content=image,
        role="product",
    )

    assert first.status_code == same_message.status_code == other_message.status_code == 201
    assert same_message.json()["id"] == first.json()["id"]
    assert same_message.json()["deduplicated"] is True
    assert other_message.json()["id"] != first.json()["id"]
    assert other_message.json()["deduplicated"] is True
    assert storage.put_calls == 1
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_asset_operations_enforce_user_and_project_scope(
    asset_api: tuple[AsyncClient, FakeAssetStorage],
) -> None:
    client, storage = asset_api
    headers = _headers()
    message_id = await _message(client, headers)
    uploaded = await _upload(client, headers, message_id)
    asset_id = uploaded.json()["id"]

    for header_name in ("X-User-ID", "X-Project-ID"):
        wrong_headers = dict(headers)
        wrong_headers[header_name] = str(uuid4())
        assert (
            await client.get(f"/api/assets/{asset_id}", headers=wrong_headers)
        ).status_code == 404
        assert (
            await client.get(
                "/api/assets",
                headers=wrong_headers,
                params={"message_id": message_id},
            )
        ).status_code == 404
        assert (
            await _upload(client, wrong_headers, message_id, filename="other.png")
        ).status_code == 404

    assert storage.put_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "filename", "mime_type", "expected_code"),
    [
        (b"", "empty.png", "image/png", "empty_file"),
        (b"not-an-image", "broken.png", "image/png", "invalid_asset"),
        (_image_bytes(), "wrong.jpg", "image/jpeg", "mime_type_mismatch"),
        (_image_bytes("GIF"), "animated.gif", "image/gif", "unsupported_mime_type"),
    ],
)
async def test_invalid_uploads_are_rejected_before_storage(
    asset_api: tuple[AsyncClient, FakeAssetStorage],
    content: bytes,
    filename: str,
    mime_type: str,
    expected_code: str,
) -> None:
    client, storage = asset_api
    headers = _headers()
    message_id = await _message(client, headers)

    response = await _upload(
        client,
        headers,
        message_id,
        content=content,
        filename=filename,
        mime_type=mime_type,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    assert storage.put_calls == 0


def test_upload_validation_enforces_size_and_dimension_limits() -> None:
    image = _image_bytes(size=(20, 10))
    with pytest.raises(ValueError, match="byte limit"):
        validate_image_upload(
            data=image,
            original_filename="image.png",
            declared_mime_type="image/png",
            max_size_bytes=len(image) - 1,
            max_dimension=100,
            max_pixels=10_000,
        )
    with pytest.raises(ValueError, match="dimension"):
        validate_image_upload(
            data=image,
            original_filename="image.png",
            declared_mime_type="image/png",
            max_size_bytes=len(image),
            max_dimension=19,
            max_pixels=10_000,
        )


@pytest.mark.asyncio
async def test_stream_reader_rejects_content_beyond_limit() -> None:
    upload = UploadFile(file=BytesIO(b"four"), filename="large.png")
    with pytest.raises(AssetValidationError) as error:
        await _read_upload(upload, limit=3)
    assert error.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_repository_failure_compensates_new_storage_object() -> None:
    class FailingRepository:
        async def message_exists(self, **_kwargs) -> bool:
            return True

        async def find_by_checksum(self, **_kwargs):
            return None

        async def create(self, **_kwargs):
            raise RuntimeError("database write failed")

    storage = FakeAssetStorage()
    service = AssetService(
        repository=cast(AssetRepository, FailingRepository()),
        storage=storage,
        max_size_bytes=1_000_000,
        max_dimension=1_000,
        max_pixels=1_000_000,
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        await service.upload(
            scope=ConversationScope(user_id=uuid4(), project_id=uuid4()),
            message_id=uuid4(),
            role=AssetRole.LOGO,
            original_filename="logo.png",
            declared_mime_type="image/png",
            data=_image_bytes(),
        )
    assert storage.put_calls == 1
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_storage_failure_returns_service_unavailable_without_asset_row(
    asset_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = FakeAssetStorage(fail_upload=True)

    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with asset_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    app.dependency_overrides[get_asset_storage] = lambda: storage
    headers = _headers()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            message_id = await _message(client, headers)
            response = await _upload(client, headers, message_id)
            assert response.status_code == 503
            listed = await client.get(
                "/api/assets",
                headers=headers,
                params={"message_id": message_id},
            )
            assert listed.status_code == 200
            assert listed.json() == []
    finally:
        app.dependency_overrides.clear()
