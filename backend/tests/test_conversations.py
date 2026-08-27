from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.models.conversations import ConversationModel
from app.shared.conversations.domain import ConversationScope


@pytest_asyncio.fixture
async def conversation_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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
async def conversation_client(
    conversation_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with conversation_session_factory.begin() as session:
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


def _headers(*, user_id: str | None = None, project_id: str | None = None) -> dict[str, str]:
    return {
        "X-User-ID": user_id or str(uuid4()),
        "X-Project-ID": project_id or str(uuid4()),
    }


async def _create_conversation(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    title: str | None = "AtomX Rent",
    conversation_type: str = "post",
) -> dict[str, Any]:
    response = await client.post(
        "/api/conversations",
        headers=headers,
        json={"title": title, "type": conversation_type},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_conversation_list_is_strictly_filtered_by_type(
    conversation_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _create_conversation(
        conversation_client, headers, title="Post", conversation_type="post"
    )
    campaign = await _create_conversation(
        conversation_client, headers, title="Campaign", conversation_type="campaign"
    )

    posts = await conversation_client.get(
        "/api/conversations", headers=headers, params={"type": "post"}
    )
    campaigns = await conversation_client.get(
        "/api/conversations", headers=headers, params={"type": "campaign"}
    )

    assert [item["id"] for item in posts.json()] == [post["id"]]
    assert [item["id"] for item in campaigns.json()] == [campaign["id"]]
    assert all(item["type"] == "post" for item in posts.json())


@pytest.mark.asyncio
async def test_section_apis_create_and_isolate_conversation_histories(
    conversation_client: AsyncClient,
) -> None:
    headers = _headers()
    post_response = await conversation_client.post(
        "/api/posts/conversations", headers=headers, json={"title": "Post chat"}
    )
    campaign_response = await conversation_client.post(
        "/api/campaigns/conversations",
        headers=headers,
        json={"title": "Campaign chat", "type": "post"},
    )
    assert post_response.status_code == 201
    assert campaign_response.status_code == 201
    post = post_response.json()
    campaign = campaign_response.json()
    assert post["type"] == "post"
    assert campaign["type"] == "campaign"

    posts = await conversation_client.get("/api/posts/conversations", headers=headers)
    campaigns = await conversation_client.get("/api/campaigns/conversations", headers=headers)
    assert [item["id"] for item in posts.json()] == [post["id"]]
    assert [item["id"] for item in campaigns.json()] == [campaign["id"]]

    wrong_section = await conversation_client.get(
        f"/api/posts/conversations/{campaign['id']}", headers=headers
    )
    wrong_history = await conversation_client.get(
        f"/api/campaigns/conversations/{post['id']}/messages", headers=headers
    )
    assert wrong_section.status_code == 404
    assert wrong_history.status_code == 404
    assert all(item["type"] == "campaign" for item in campaigns.json())


@pytest.mark.asyncio
async def test_full_conversation_lifecycle_preserves_context_and_unicode(
    conversation_client: AsyncClient,
) -> None:
    headers = _headers()
    created = await _create_conversation(
        conversation_client,
        headers,
        title="  AtomX Rent Instagram post  ",
    )
    conversation_id = created["id"]
    assert created["title"] == "AtomX Rent Instagram post"
    assert created["project_id"] == headers["X-Project-ID"]

    retrieved = await conversation_client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
    )
    assert retrieved.status_code == 200
    assert retrieved.json() == created

    empty_history = await conversation_client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert empty_history.status_code == 200
    assert empty_history.json()["items"] == []
    assert empty_history.json()["total"] == 0

    messages = [
        ("user", "Kam një rent-a-car në Prishtinë.", {"language": "sq"}),
        ("assistant", "Si quhet biznesi?", {"step": 1}),
        ("system", "Ruaj kontekstin kronologjik.", {"nested": {"valid": True}}),
        ("tool", "car.jpg", {"asset": ["car.jpg"], "score": 0.98}),
    ]
    for expected_sequence, (role, content, metadata) in enumerate(messages, start=1):
        response = await conversation_client.post(
            f"/api/conversations/{conversation_id}/messages",
            headers=headers,
            json={"role": role, "content": content, "metadata": metadata},
        )
        assert response.status_code == 201
        assert response.json()["sequence"] == expected_sequence
        assert response.json()["role"] == role
        assert response.json()["content"] == content
        assert response.json()["metadata"] == metadata

    page = await conversation_client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        params={"offset": 1, "limit": 2},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert page.json()["offset"] == 1
    assert page.json()["limit"] == 2
    assert [item["sequence"] for item in page.json()["items"]] == [2, 3]
    assert [item["content"] for item in page.json()["items"]] == [
        messages[1][1],
        messages[2][1],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["conversation", "history", "append"])
@pytest.mark.parametrize("wrong_scope", ["user", "project"])
async def test_every_conversation_operation_enforces_scope(
    conversation_client: AsyncClient,
    resource: str,
    wrong_scope: str,
) -> None:
    headers = _headers()
    conversation = await _create_conversation(conversation_client, headers)
    scoped_headers = dict(headers)
    scoped_headers[f"X-{wrong_scope.title()}-ID"] = str(uuid4())
    conversation_path = f"/api/conversations/{conversation['id']}"

    if resource == "conversation":
        response = await conversation_client.get(conversation_path, headers=scoped_headers)
    elif resource == "history":
        response = await conversation_client.get(
            f"{conversation_path}/messages",
            headers=scoped_headers,
        )
    else:
        response = await conversation_client.post(
            f"{conversation_path}/messages",
            headers=scoped_headers,
            json={"content": "Must remain isolated"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["conversation", "history", "append"])
async def test_missing_conversation_returns_not_found(
    conversation_client: AsyncClient,
    resource: str,
) -> None:
    headers = _headers()
    conversation_path = f"/api/conversations/{uuid4()}"
    if resource == "conversation":
        response = await conversation_client.get(conversation_path, headers=headers)
    elif resource == "history":
        response = await conversation_client.get(
            f"{conversation_path}/messages",
            headers=headers,
        )
    else:
        response = await conversation_client.post(
            f"{conversation_path}/messages",
            headers=headers,
            json={"content": "Missing conversation"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 422),
        ({"X-User-ID": "not-a-uuid", "X-Project-ID": str(uuid4())}, 422),
        ({"X-User-ID": str(uuid4()), "X-Project-ID": "not-a-uuid"}, 422),
    ],
)
async def test_scope_headers_are_required_uuid_values(
    conversation_client: AsyncClient,
    headers: dict[str, str],
    expected_status: int,
) -> None:
    response = await conversation_client.post(
        "/api/conversations",
        headers=headers,
        json={},
    )
    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": ""},
        {"content": "   "},
        {"content": "x" * 50_001},
        {"content": "valid", "role": "invalid-role"},
        {"content": "valid", "metadata": ["must", "be", "an", "object"]},
        {"content": "valid", "metadata": None},
    ],
)
async def test_invalid_messages_are_rejected_without_being_persisted(
    conversation_client: AsyncClient,
    payload: dict[str, Any],
) -> None:
    headers = _headers()
    conversation = await _create_conversation(conversation_client, headers)
    path = f"/api/conversations/{conversation['id']}/messages"

    response = await conversation_client.post(path, headers=headers, json=payload)
    assert response.status_code == 422

    history = await conversation_client.get(path, headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"offset": -1},
        {"limit": 0},
        {"limit": 101},
        {"offset": "invalid"},
        {"limit": "invalid"},
    ],
)
async def test_invalid_pagination_is_rejected(
    conversation_client: AsyncClient,
    params: dict[str, Any],
) -> None:
    headers = _headers()
    conversation = await _create_conversation(conversation_client, headers)
    response = await conversation_client.get(
        f"/api/conversations/{conversation['id']}/messages",
        headers=headers,
        params=params,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_conversation_title_validation_and_normalization(
    conversation_client: AsyncClient,
) -> None:
    headers = _headers()
    blank = await _create_conversation(conversation_client, headers, title="   ")
    assert blank["title"] is None

    too_long = await conversation_client.post(
        "/api/conversations",
        headers=headers,
        json={"title": "x" * 201},
    )
    assert too_long.status_code == 422


@pytest.mark.asyncio
async def test_exact_size_defaults_and_pagination_boundaries(
    conversation_client: AsyncClient,
) -> None:
    headers = _headers()
    title = "t" * 200
    conversation = await _create_conversation(conversation_client, headers, title=title)
    assert conversation["title"] == title

    content = "x" * 50_000
    appended = await conversation_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": content},
    )
    assert appended.status_code == 201
    assert appended.json()["role"] == "user"
    assert appended.json()["metadata"] == {}
    assert appended.json()["content"] == content

    beyond_end = await conversation_client.get(
        f"/api/conversations/{conversation['id']}/messages",
        headers=headers,
        params={"offset": 10, "limit": 100},
    )
    assert beyond_end.status_code == 200
    assert beyond_end.json()["total"] == 1
    assert beyond_end.json()["items"] == []

    invalid_id = await conversation_client.get(
        "/api/conversations/not-a-uuid",
        headers=headers,
    )
    assert invalid_id.status_code == 422


@pytest.mark.asyncio
async def test_failed_transaction_rolls_back_created_conversation(
    conversation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = None
    with pytest.raises(RuntimeError, match="force rollback"):
        async with conversation_session_factory.begin() as session:
            repository = SQLAlchemyConversationRepository(session)
            conversation = await repository.create(
                scope=ConversationScope(user_id=uuid4(), project_id=uuid4()),
                title="Must roll back",
            )
            conversation_id = conversation.id
            raise RuntimeError("force rollback")

    assert conversation_id is not None
    async with conversation_session_factory() as session:
        count = await session.scalar(
            select(func.count(ConversationModel.id)).where(ConversationModel.id == conversation_id)
        )
    assert count == 0
