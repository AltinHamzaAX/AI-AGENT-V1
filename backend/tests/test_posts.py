from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.execution_traces import (
    SQLAlchemyExecutionTraceRecorder,
)
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.models.posts import PostGenerationStateModel
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import (
    GenerationArtifactKind,
    GenerationStatus,
    PostWorkflowSection,
)
from app.modules.posts.domain.exceptions import SemanticContractHardFailError
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    ExecutionTraceCreate,
)
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


def _semantic_contract_payload(
    *,
    expected_version: int = 1,
    required_asset_id: str | None = None,
) -> dict:
    return {
        "expected_version": expected_version,
        "company": "Promotiva Mobility",
        "brand": "Škoda",
        "product": "Škoda Fabia",
        "primary_entity": "Škoda Fabia rental",
        "goal": "Drive bookings",
        "audience": "Travelers needing a compact rental car",
        "market": "Kosovo",
        "location": "Prishtina",
        "offer": "€35/day",
        "cta_intent": "Book now",
        "platform": "Instagram",
        "language": "Albanian",
        "required_facts": {"price": "€35/day", "model": "Škoda Fabia"},
        "forbidden_claims": ["cheapest rental in Kosovo"],
        "required_assets": [required_asset_id] if required_asset_id else [],
        "constraints": ["Do not replace the product or logo"],
    }


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
        assert generation["status"] == "queued"
        assert generation["job_status"] == "queued"
        assert generation["deduplicated"] is False
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
async def test_generation_trace_timeline_is_persisted_scoped_and_readable(
    post_client: AsyncClient,
    post_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = _headers()
    post = await _post(post_client, headers, {"title": "Trace timeline"})
    generation_response = await post_client.post(
        f"/api/posts/{post['id']}/generations",
        headers=headers,
    )
    generation = generation_response.json()
    correlation_id = uuid4()
    await SQLAlchemyExecutionTraceRecorder(post_session_factory).record(
        ExecutionTraceCreate(
            generation_id=UUID(generation["id"]),
            correlation_id=correlation_id,
            kind=ExecutionRunKind.GENERATION_STEP,
            name="client_understanding",
            status=ExecutionRunStatus.SUCCEEDED,
            input_reference="sha256:" + "a" * 64,
            output_reference="sha256:" + "b" * 64,
            duration_ms=2200,
        )
    )

    response = await post_client.get(
        f"/api/posts/{post['id']}/generations/{generation['id']}/traces",
        headers=headers,
    )

    assert response.status_code == 200
    trace = response.json()[0]
    assert trace["kind"] == "generation_step"
    assert trace["name"] == "client_understanding"
    assert trace["duration_ms"] == 2200
    assert trace["correlation_id"] == str(correlation_id)

    forbidden = await post_client.get(
        f"/api/posts/{post['id']}/generations/{generation['id']}/traces",
        headers=_headers(),
    )
    assert forbidden.status_code == 404


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
@pytest.mark.parametrize(
    "value",
    [
        {"timestamp": datetime.now(UTC)},
        {"score": float("nan")},
        {1: "non-string key"},
    ],
)
async def test_internal_workflow_writes_reject_non_json_values(
    post_session_factory: async_sessionmaker[AsyncSession],
    value: dict,
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
        with pytest.raises(ValueError):
            await service.write_workflow_section(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
                section=PostWorkflowSection.BRIEF,
                value=value,
                expected_version=1,
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


@pytest.mark.asyncio
async def test_generation_workflow_state_is_complete_versioned_and_persistent(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    generation_response = await post_client.post(
        f"/api/posts/{post['id']}/generations",
        headers=headers,
    )
    generation = generation_response.json()
    base_path = f"/api/posts/{post['id']}/generations/{generation['id']}/state"

    initial = await post_client.get(base_path, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["version"] == 1
    assert initial.json()["schema_version"] == 5
    assert initial.json()["state"] == {
        "supervisor": {},
        "conversation_context": {},
        "brief": {},
        "semantic_contract": {},
        "brand": {},
        "product": {},
        "assets": [],
        "audience": {},
        "research": {},
        "marketing_strategy": {},
        "creative_concept": {},
        "copy": {},
        "art_direction": {},
        "design_spec": {},
        "generation_plan": {},
        "generation_artifacts": [],
        "scene_purity": {},
        "post_draft": {},
        "quality": {},
        "design_quality": {},
        "revision_history": [],
    }

    protected = await post_client.patch(
        f"{base_path}/supervisor",
        headers=headers,
        json={"expected_version": 1, "value": {"current_stage": "production"}},
    )
    assert protected.status_code == 422
    assert protected.json()["detail"] == (
        "supervisor state is internal and cannot be written through API"
    )

    brief = {"goal": "Launch", "platform": "instagram"}
    written = await post_client.patch(
        f"{base_path}/brief",
        headers=headers,
        json={"expected_version": 1, "value": brief},
    )
    assert written.status_code == 200
    assert written.json()["version"] == 2
    assert written.json()["state"]["brief"] == brief

    assets = [{"asset_id": str(uuid4()), "role": "logo"}]
    written_assets = await post_client.patch(
        f"{base_path}/assets",
        headers=headers,
        json={"expected_version": 2, "value": assets},
    )
    assert written_assets.status_code == 200
    assert written_assets.json()["version"] == 3
    assert written_assets.json()["state"]["brief"] == brief
    assert written_assets.json()["state"]["assets"] == assets

    retrieved = await post_client.get(base_path, headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json()["version"] == written_assets.json()["version"]
    assert retrieved.json()["state"] == written_assets.json()["state"]

    versions = await post_client.get(f"{base_path}/versions", headers=headers)
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1, 2, 3]
    assert [item["changed_section"] for item in versions.json()] == [
        None,
        "brief",
        "assets",
    ]
    assert versions.json()[0]["state"]["brief"] == {}
    assert versions.json()[1]["state"]["brief"] == brief
    assert versions.json()[1]["state"]["assets"] == []


@pytest.mark.asyncio
async def test_workflow_state_rejects_stale_wrong_shape_and_cross_scope_writes(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    generation = (
        await post_client.post(
            f"/api/posts/{post['id']}/generations",
            headers=headers,
        )
    ).json()
    base_path = f"/api/posts/{post['id']}/generations/{generation['id']}/state"

    first = await post_client.patch(
        f"{base_path}/quality",
        headers=headers,
        json={"expected_version": 1, "value": {"score": 0.9}},
    )
    assert first.status_code == 200

    stale = await post_client.patch(
        f"{base_path}/quality",
        headers=headers,
        json={"expected_version": 1, "value": {"score": 0.1}},
    )
    assert stale.status_code == 409
    assert (await post_client.get(base_path, headers=headers)).json()["state"]["quality"] == {
        "score": 0.9
    }

    wrong_object = await post_client.patch(
        f"{base_path}/brief",
        headers=headers,
        json={"expected_version": 2, "value": []},
    )
    assert wrong_object.status_code == 422
    wrong_array = await post_client.patch(
        f"{base_path}/revision_history",
        headers=headers,
        json={"expected_version": 2, "value": {}},
    )
    assert wrong_array.status_code == 422

    wrong_headers = {**headers, "X-Project-ID": str(uuid4())}
    assert (await post_client.get(base_path, headers=wrong_headers)).status_code == 404
    assert (
        await post_client.patch(
            f"{base_path}/brief",
            headers=wrong_headers,
            json={"expected_version": 2, "value": {}},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_semantic_contract_is_created_once_persisted_and_idempotent(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    generation = (
        await post_client.post(f"/api/posts/{post['id']}/generations", headers=headers)
    ).json()
    contract_path = f"/api/posts/{post['id']}/generations/{generation['id']}/semantic-contract"
    payload = _semantic_contract_payload()

    missing = await post_client.get(contract_path, headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Semantic contract not found"}

    created = await post_client.put(contract_path, headers=headers, json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["state_version"] == 2
    assert body["contract"]["contract_version"] == 1
    assert body["contract"]["product"] == "Škoda Fabia"
    assert body["contract"]["offer"] == "€35/day"
    assert len(body["contract"]["fingerprint"]) == 64

    retrieved = await post_client.get(contract_path, headers=headers)
    assert retrieved.status_code == 200
    assert retrieved.json() == body

    repeated = await post_client.put(contract_path, headers=headers, json=payload)
    assert repeated.status_code == 200
    assert repeated.json() == body

    state_path = f"/api/posts/{post['id']}/generations/{generation['id']}/state"
    state = await post_client.get(state_path, headers=headers)
    assert state.status_code == 200
    assert state.json()["state"]["semantic_contract"] == body["contract"]
    history = await post_client.get(f"{state_path}/versions", headers=headers)
    assert [version["version"] for version in history.json()] == [1, 2]
    assert history.json()[1]["changed_section"] == "semantic_contract"


@pytest.mark.asyncio
async def test_semantic_contract_replacement_and_generic_mutation_hard_fail(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    generation = (
        await post_client.post(f"/api/posts/{post['id']}/generations", headers=headers)
    ).json()
    root = f"/api/posts/{post['id']}/generations/{generation['id']}"
    payload = _semantic_contract_payload()
    created = await post_client.put(
        f"{root}/semantic-contract",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 200

    replacement = {**payload, "expected_version": 2, "product": "BMW", "offer": "€25/day"}
    rejected = await post_client.put(
        f"{root}/semantic-contract",
        headers=headers,
        json=replacement,
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["decision"] == "HARD_FAIL"
    assert rejected.json()["detail"]["code"] == "SEMANTIC_CONTRACT_HARD_FAIL"

    generic = await post_client.patch(
        f"{root}/state/semantic_contract",
        headers=headers,
        json={"expected_version": 2, "value": {"product": "BMW"}},
    )
    assert generic.status_code == 409
    assert generic.json()["detail"]["decision"] == "HARD_FAIL"

    unchanged = await post_client.get(f"{root}/semantic-contract", headers=headers)
    assert unchanged.json()["contract"]["product"] == "Škoda Fabia"
    assert unchanged.json()["contract"]["offer"] == "€35/day"
    assert unchanged.json()["state_version"] == 2


@pytest.mark.asyncio
async def test_semantic_contract_validation_continues_or_hard_fails_deterministically(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    required_asset_id = str(uuid4())
    post = await _post(post_client, headers)
    generation = (
        await post_client.post(f"/api/posts/{post['id']}/generations", headers=headers)
    ).json()
    root = f"/api/posts/{post['id']}/generations/{generation['id']}"
    created = await post_client.put(
        f"{root}/semantic-contract",
        headers=headers,
        json=_semantic_contract_payload(required_asset_id=required_asset_id),
    )
    assert created.status_code == 200
    fingerprint = created.json()["contract"]["fingerprint"]

    valid = await post_client.post(
        f"{root}/semantic-contract/validate",
        headers=headers,
        json={
            "contract_fingerprint": fingerprint,
            "product": "  škoda   fabia ",
            "offer": "€35/day",
            "required_facts": {"price": "€35/day"},
            "claims": ["Reliable compact rental"],
            "used_assets": [required_asset_id],
        },
    )
    assert valid.status_code == 200
    assert valid.json() == {
        "valid": True,
        "decision": "CONTINUE",
        "fingerprint": fingerprint,
    }

    invalid_cases = [
        ({"contract_fingerprint": "0" * 64}, "fingerprint"),
        ({"product": "BMW"}, "product changed"),
        ({"required_facts": {" PRICE ": "€25/day"}}, "required fact"),
        ({"claims": ["The cheapest rental in Kosovo"]}, "forbidden claim"),
        ({"used_assets": []}, "required asset missing"),
    ]
    for assertions, expected_violation in invalid_cases:
        response = await post_client.post(
            f"{root}/semantic-contract/validate",
            headers=headers,
            json=assertions,
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["decision"] == "HARD_FAIL"
        assert any(expected_violation in violation for violation in detail["violations"])


@pytest.mark.asyncio
async def test_semantic_contract_validates_input_scope_and_state_version(
    post_client: AsyncClient,
) -> None:
    headers = _headers()
    post = await _post(post_client, headers)
    generation = (
        await post_client.post(f"/api/posts/{post['id']}/generations", headers=headers)
    ).json()
    path = f"/api/posts/{post['id']}/generations/{generation['id']}/semantic-contract"

    stale = await post_client.put(
        path,
        headers=headers,
        json=_semantic_contract_payload(expected_version=2),
    )
    assert stale.status_code == 409

    invalid = _semantic_contract_payload()
    invalid["primary_entity"] = "   "
    assert (await post_client.put(path, headers=headers, json=invalid)).status_code == 422

    wrong_headers = {**headers, "X-User-ID": str(uuid4())}
    assert (
        await post_client.put(
            path,
            headers=wrong_headers,
            json=_semantic_contract_payload(),
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_tampered_or_legacy_semantic_contract_hard_fails_integrity_check(
    post_session_factory: async_sessionmaker[AsyncSession],
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
        state_model = await session.get(PostGenerationStateModel, generation.id)
        assert state_model is not None
        tampered_state = dict(state_model.state)
        tampered_state["semantic_contract"] = {"product": "BMW"}
        state_model.state = tampered_state

    async with post_session_factory() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        with pytest.raises(
            SemanticContractHardFailError,
            match="Semantic contract violation",
        ):
            await service.get_semantic_contract(
                generation_id=generation.id,
                post_id=post.id,
                scope=scope,
            )


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
