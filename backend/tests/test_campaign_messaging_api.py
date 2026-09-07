import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.dependencies.providers import get_llm_provider
from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.campaigns import SQLAlchemyCampaignRepository
from app.infrastructure.database.session import get_db_transaction
from app.main import app
from app.modules.campaigns.domain import CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderError
from app.shared.conversations.domain import ConversationScope


class ScriptedCampaignLLM:
    def __init__(self) -> None:
        self.responses: list[dict[str, Any] | str] = []
        self.requests: list[LLMRequest] = []
        self.failure: Exception | None = None

    def script(self, payload: dict[str, Any] | str) -> None:
        self.responses.append(payload)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        payload = self.responses.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return LLMResponse(text=text, provider="scripted", model="campaign-test")


@pytest_asyncio.fixture
async def campaign_api_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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
async def campaign_api(
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncClient, ScriptedCampaignLLM]]:
    llm = ScriptedCampaignLLM()

    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with campaign_api_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    app.dependency_overrides[get_llm_provider] = lambda: llm
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, llm
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid4()), "X-Project-ID": str(uuid4())}


async def _campaign(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    headers: dict[str, str],
    *,
    brief: CampaignBrief | None = None,
    status: CampaignStatus | None = None,
) -> tuple[str, str]:
    response = await client.post(
        "/api/campaigns/conversations",
        headers=headers,
        json={"title": "Campaign test"},
    )
    assert response.status_code == 201
    conversation_id = response.json()["id"]
    scope = ConversationScope(
        user_id=UUID(headers["X-User-ID"]),
        project_id=UUID(headers["X-Project-ID"]),
    )
    async with session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        campaign = await repository.create_campaign(
            conversation_id=UUID(conversation_id),
            initial_brief=brief or CampaignBrief(),
        )
        if status is not None:
            updated = await repository.update_status(
                campaign_id=campaign.id,
                scope=scope,
                status=status,
            )
            assert updated is not None
            campaign = updated
    return str(campaign.id), conversation_id


async def _history(
    client: AsyncClient,
    headers: dict[str, str],
    conversation_id: str,
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/api/campaigns/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["items"]


@pytest.mark.asyncio
async def test_campaign_message_updates_brief_state_and_shared_history(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, llm = campaign_api
    headers = _headers()
    campaign_id, conversation_id = await _campaign(
        client,
        campaign_api_session_factory,
        headers,
    )
    llm.script(
        {
            "reply": "Shkelqyeshem. Kampanja ka informacion te mjaftueshem.",
            "extracted_fields": {
                "business": "FitZone Gym",
                "product_or_service": "Gym membership",
                "goal": "Acquire new customers",
                "audience": "Students",
                "location": "Prishtina",
            },
        }
    )

    response = await client.post(
        f"/api/campaigns/{campaign_id}/messages",
        headers=headers,
        json={"message": "Kam nje gym ne Prishtine dhe dua me shume kliente studente."},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reply"] == "Shkelqyeshem. Kampanja ka informacion te mjaftueshem."
    assert body["status"] == "READY"
    assert body["brief"] == {
        "business": "FitZone Gym",
        "product_or_service": "Gym membership",
        "goal": "Acquire new customers",
        "audience": "Students",
        "location": "Prishtina",
        "offer": None,
        "value_proposition": None,
        "channels": None,
        "budget_amount": None,
        "budget_currency": None,
        "duration": None,
        "brand_tone": None,
        "constraints": None,
    }
    assert len(llm.requests) == 1
    assert llm.requests[0].response_format == "json"

    history = await _history(client, headers, conversation_id)
    assert [(item["sequence"], item["role"]) for item in history] == [
        (1, "user"),
        (2, "assistant"),
    ]
    assert history[0]["content"].startswith("Kam nje gym")
    assert history[1]["content"] == body["reply"]

    scope = ConversationScope(
        user_id=UUID(headers["X-User-ID"]),
        project_id=UUID(headers["X-Project-ID"]),
    )
    async with campaign_api_session_factory() as session:
        repository = SQLAlchemyCampaignRepository(session)
        persisted_campaign = await repository.get_campaign(
            campaign_id=UUID(campaign_id),
            scope=scope,
        )
        assert persisted_campaign is not None
        assert persisted_campaign.status.value == body["status"]
        assert await repository.get_brief(
            campaign_id=UUID(campaign_id),
            scope=scope,
        ) == CampaignBrief.model_validate(body["brief"])
        assert await repository.get_plan(
            campaign_id=UUID(campaign_id),
            scope=scope,
        ) is None


@pytest.mark.asyncio
async def test_campaign_message_no_op_repairs_sufficient_briefing_state(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, llm = campaign_api
    headers = _headers()
    initial = CampaignBrief(
        business="FitZone",
        goal="Acquire customers",
        audience="Students",
        location="Prishtina",
    )
    campaign_id, _ = await _campaign(
        client,
        campaign_api_session_factory,
        headers,
        brief=initial,
    )
    llm.script({"reply": "Po, mund t'ju ndihmoj.", "extracted_fields": {}})

    response = await client.post(
        f"/api/campaigns/{campaign_id}/messages",
        headers=headers,
        json={"message": "A mund te me ndihmosh me kampanjen?"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "READY"
    assert response.json()["brief"] == initial.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CampaignStatus.READY, CampaignStatus.PLAN_READY])
async def test_campaign_message_no_op_preserves_valid_lifecycle_state(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
    status: CampaignStatus,
) -> None:
    client, llm = campaign_api
    headers = _headers()
    initial = CampaignBrief(
        business="FitZone",
        goal="Acquire customers",
        audience="Students",
    )
    campaign_id, _ = await _campaign(
        client,
        campaign_api_session_factory,
        headers,
        brief=initial,
        status=status,
    )
    llm.script({"reply": "Brief-i mbetet i njejte.", "extracted_fields": {}})

    response = await client.post(
        f"/api/campaigns/{campaign_id}/messages",
        headers=headers,
        json={"message": "Vazhdo me te njejtat informata."},
    )

    assert response.status_code == 201
    assert response.json()["status"] == status.value
    assert response.json()["brief"] == initial.model_dump(mode="json")


@pytest.mark.asyncio
async def test_campaign_message_hides_out_of_scope_campaign(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, llm = campaign_api
    owner_headers = _headers()
    campaign_id, conversation_id = await _campaign(
        client,
        campaign_api_session_factory,
        owner_headers,
    )

    response = await client.post(
        f"/api/campaigns/{campaign_id}/messages",
        headers=_headers(),
        json={"message": "Try another user's campaign"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Campaign not found"}
    assert llm.requests == []
    assert await _history(client, owner_headers, conversation_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "   "])
async def test_campaign_message_rejects_empty_input(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    message: str,
) -> None:
    client, llm = campaign_api
    response = await client.post(
        f"/api/campaigns/{uuid4()}/messages",
        headers=_headers(),
        json={"message": message},
    )
    assert response.status_code == 422
    assert llm.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (ProviderError("provider secret detail"), 502),
        (None, 502),
    ],
)
async def test_campaign_message_provider_failures_are_safe_and_atomic(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
    failure: Exception | None,
    expected_status: int,
) -> None:
    client, llm = campaign_api
    headers = _headers()
    campaign_id, conversation_id = await _campaign(
        client,
        campaign_api_session_factory,
        headers,
        brief=CampaignBrief(business="Original"),
    )
    if failure is None:
        llm.script("not valid json")
    else:
        llm.failure = failure

    response = await client.post(
        f"/api/campaigns/{campaign_id}/messages",
        headers=headers,
        json={"message": "This turn must roll back"},
    )

    assert response.status_code == expected_status
    assert "provider secret detail" not in response.text
    assert response.json()["detail"].endswith("the turn was not saved")
    assert await _history(client, headers, conversation_id) == []


@pytest.mark.asyncio
async def test_campaign_message_returns_not_found_for_unknown_campaign(
    campaign_api: tuple[AsyncClient, ScriptedCampaignLLM],
) -> None:
    client, llm = campaign_api
    response = await client.post(
        f"/api/campaigns/{uuid4()}/messages",
        headers=_headers(),
        json={"message": "Start a campaign"},
    )
    assert response.status_code == 404
    assert llm.requests == []
