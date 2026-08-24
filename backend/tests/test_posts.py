from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import GenerationArtifactKind, GenerationStatus
from app.modules.posts.services import PostsService


@pytest_asyncio.fixture
async def post_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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
async def post_client(
    post_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with post_session_factory.begin() as session:
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


async def _conversation(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/conversations",
        headers=headers,
        json={"title": "Post source"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def _post(
    client: AsyncClient,
    headers: dict[str, str],
    payload: dict | None = None,
) -> dict:
    response = await client.post("/api/posts", headers=headers, json=payload or {})
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_standalone_post_supports_multiple_ordered_generation_attempts(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers, {"title": "  Instagram launch  "})
    assert post["title"] == "Instagram launch"
    assert post["conversation_id"] is None
    assert post["campaign_id"] is None
    assert post["project_id"] == headers["X-Project-ID"]

    retrieved = await post_client.get(f"/api/posts/{post['id']}", headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json() == post

    created = []
    for expected_attempt in range(1, 4):
        response = await post_client.post(
            f"/api/posts/{post['id']}/generations",
            headers=headers,
        )
        assert response.status_code == 201
        generation = response.json()
        assert generation["attempt"] == expected_attempt
        assert generation["status"] == "pending"
        created.append(generation)

    listed = await post_client.get(
        f"/api/posts/{post['id']}/generations",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json() == created

    artifacts = await post_client.get(
        f"/api/posts/{post['id']}/generations/{created[0]['id']}/artifacts",
        headers=headers,
    )
    assert artifacts.status_code == 200
    assert artifacts.json() == []


@pytest.mark.asyncio
async def test_conversation_and_future_campaign_posts_use_the_same_model(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    conversation_id = await _conversation(post_client, headers)
    campaign_id = str(uuid4())

    conversation_post = await _post(
        post_client,
        headers,
        {"conversation_id": conversation_id, "title": "Chat post"},
    )
    campaign_post = await _post(
        post_client,
        headers,
        {"campaign_id": campaign_id, "title": "Campaign post"},
    )
    combined_post = await _post(
        post_client,
        headers,
        {
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "title": "Campaign chat post",
        },
    )

    assert conversation_post["conversation_id"] == conversation_id
    assert conversation_post["campaign_id"] is None
    assert campaign_post["conversation_id"] is None
    assert campaign_post["campaign_id"] == campaign_id
    assert combined_post["conversation_id"] == conversation_id
    assert combined_post["campaign_id"] == campaign_id


@pytest.mark.asyncio
async def test_post_creation_rejects_missing_or_cross_scope_conversation(
    post_client: AsyncClient,
) -> None:
    owner_headers = _headers()
    conversation_id = await _conversation(post_client, owner_headers)

    for headers in (
        {**owner_headers, "X-User-ID": str(uuid4())},
        {**owner_headers, "X-Project-ID": str(uuid4())},
    ):
        response = await post_client.post(
            "/api/posts",
            headers=headers,
            json={"conversation_id": conversation_id},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Conversation not found"}

    missing = await post_client.post(
        "/api/posts",
        headers=owner_headers,
        json={"conversation_id": str(uuid4())},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource",
    ["post", "create-generation", "generations", "artifacts"],
)
@pytest.mark.parametrize("scope_field", ["X-User-ID", "X-Project-ID"])
async def test_every_post_operation_enforces_scope(
    post_client: AsyncClient,
    resource: str,
    scope_field: str,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    wrong_headers = {**headers, scope_field: str(uuid4())}
    path = f"/api/posts/{post['id']}"

    if resource == "post":
        response = await post_client.get(path, headers=wrong_headers)
    elif resource == "create-generation":
        response = await post_client.post(f"{path}/generations", headers=wrong_headers)
    elif resource == "generations":
        response = await post_client.get(f"{path}/generations", headers=wrong_headers)
    else:
        generation = await post_client.post(f"{path}/generations", headers=headers)
        assert generation.status_code == 201
        response = await post_client.get(
            f"{path}/generations/{generation.json()['id']}/artifacts",
            headers=wrong_headers,
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_generation_statuses_and_artifact_models_are_persisted(
    post_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with post_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="Domain test",
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)
        for generation_status in GenerationStatus:
            generation = await service.update_generation_status(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
                status=generation_status,
            )
            assert generation.status is generation_status

        artifact = await service.add_artifact(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
            kind=GenerationArtifactKind.PREVIEW,
            storage_key=f"generations/{generation.id}/preview.png",
            mime_type="image/png",
            size_bytes=1024,
            checksum="a" * 64,
            width=1080,
            height=1080,
            metadata={"renderer": "deterministic"},
        )
        assert artifact.kind is GenerationArtifactKind.PREVIEW
        assert artifact.width == artifact.height == 1080

        artifacts = await service.list_artifacts(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
        )
        assert [item.id for item in artifacts] == [artifact.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"size_bytes": 0},
        {"storage_key": ""},
        {"storage_key": "x" * 1025},
        {"mime_type": ""},
        {"mime_type": "x" * 101},
        {"checksum": "not-sha256"},
        {"checksum": "g" * 64},
        {"width": 100, "height": None},
        {"width": 0, "height": 100},
    ],
)
async def test_artifact_validation_rejects_invalid_metadata(
    post_session_factory: async_sessionmaker[AsyncSession],
    kwargs: dict,
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with post_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title=None,
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)
        values = {
            "size_bytes": 100,
            "storage_key": f"invalid/{uuid4()}.png",
            "mime_type": "image/png",
            "checksum": "a" * 64,
            "width": 100,
            "height": 100,
            **kwargs,
        }
        with pytest.raises(ValueError):
            await service.add_artifact(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
                kind=GenerationArtifactKind.FINAL,
                metadata={},
                **values,
            )


@pytest.mark.asyncio
async def test_title_and_request_validation(post_client: AsyncClient) -> None:
    headers = _headers()
    blank = await _post(post_client, headers, {"title": "   "})
    assert blank["title"] is None

    too_long = await post_client.post(
        "/api/posts",
        headers=headers,
        json={"title": "x" * 201},
    )
    assert too_long.status_code == 422

    for invalid_headers in (
        {},
        {"X-User-ID": "invalid", "X-Project-ID": str(uuid4())},
        {"X-User-ID": str(uuid4()), "X-Project-ID": "invalid"},
    ):
        assert (
            await post_client.post("/api/posts", headers=invalid_headers, json={})
        ).status_code == 422


def test_posts_boundaries_do_not_leak_sql_or_internal_agents() -> None:
    app_root = Path(__file__).parents[1] / "app"
    service_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (app_root / "modules" / "posts" / "services").glob("*.py")
    )
    assert "sqlalchemy" not in service_sources
    assert "app.infrastructure" not in service_sources
    assert "app.models" not in service_sources

    campaign_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (app_root / "modules" / "campaigns").rglob("*.py")
    )
    prohibited = (
        "app.modules.posts.agents",
        "app.modules.posts.tools",
        "app.modules.posts.orchestration",
    )
    assert all(marker not in campaign_sources for marker in prohibited)
