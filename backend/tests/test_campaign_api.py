import json
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.dependencies.campaigns import (
    get_campaign_export_service,
    get_campaign_plan_generator,
)
from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.campaigns import SQLAlchemyCampaignRepository
from app.infrastructure.database.session import get_db_transaction
from app.integrations.llm import ProviderError
from app.main import app
from app.modules.campaigns.domain import CampaignExportError, CampaignStatus
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services import CampaignExportService
from app.shared.conversations.domain import ConversationScope


class FakeCampaignPlanGenerator:
    def __init__(self) -> None:
        self.responses: list[CampaignPlan] = []
        self.briefs: list[CampaignBrief] = []
        self.failure: Exception | None = None

    async def generate(
        self,
        brief: CampaignBrief,
        *,
        repair_issues: tuple[str, ...] = (),
    ) -> CampaignPlan:
        self.briefs.append(brief)
        if self.failure is not None:
            raise self.failure
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


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
) -> AsyncIterator[tuple[AsyncClient, FakeCampaignPlanGenerator]]:
    generator = FakeCampaignPlanGenerator()

    async def transaction_override() -> AsyncIterator[AsyncSession]:
        async with campaign_api_session_factory.begin() as session:
            yield session

    app.dependency_overrides[get_db_transaction] = transaction_override
    app.dependency_overrides[get_campaign_plan_generator] = lambda: generator
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, generator
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"X-User-ID": str(uuid4()), "X-Project-ID": str(uuid4())}


def _ready_brief(**updates: Any) -> CampaignBrief:
    values: dict[str, Any] = {
        "business": "FitZone Gym",
        "goal": "Acquire student members",
        "audience": "Students aged 18-25",
        "location": "Prishtina",
        "channels": ["Instagram"],
    }
    values.update(updates)
    return CampaignBrief.model_validate(values)


def _plan(*, campaign_name: str = "Student Fitness", location: str = "Prishtina") -> CampaignPlan:
    return CampaignPlan.model_validate(
        {
            "campaign_name": campaign_name,
            "executive_summary": "A focused student acquisition campaign.",
            "objective": {"primary": "Acquire student members", "secondary": None},
            "target_audience": {
                "primary": "Students aged 18 to 25",
                "location": location,
                "needs_or_motivations": ["Affordable fitness"],
            },
            "offer": None,
            "value_proposition": "Flexible gym access for students.",
            "positioning": "An accessible local gym for students.",
            "key_message": "Build a healthy routine on a student budget.",
            "strategy": "Reach students with useful short-form content.",
            "channels": [
                {
                    "name": "Instagram",
                    "purpose": "Reach local students",
                    "reason": "The confirmed audience uses visual content",
                }
            ],
            "content_direction": [
                {"idea": "Student workout tips", "purpose": "Build relevance and trust"}
            ],
            "budget_allocation": None,
            "timeline": [
                {
                    "period": "Weeks 1-2",
                    "phase": "Launch",
                    "objective": "Build awareness and trials",
                    "activities": ["Publish student-focused content"],
                }
            ],
            "kpis": [{"name": "Trial signups", "purpose": "Measure acquisition"}],
            "assumptions_or_risks": [],
            "next_steps": ["Approve the content calendar"],
        }
    )


async def _conversation(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    campaign_kind: bool = True,
) -> str:
    section = "campaigns" if campaign_kind else "posts"
    response = await client.post(
        f"/api/{section}/conversations",
        headers=headers,
        json={"title": "Campaign API test"},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _campaign(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    brief: CampaignBrief | None = None,
) -> dict[str, Any]:
    conversation_id = await _conversation(client, headers)
    payload: dict[str, Any] = {"conversation_id": conversation_id}
    if brief is not None:
        payload["brief"] = brief.model_dump(mode="json")
    response = await client.post("/api/campaigns", headers=headers, json=payload)
    assert response.status_code == 201
    return response.json()


def _scope(headers: dict[str, str]) -> ConversationScope:
    return ConversationScope(
        user_id=UUID(headers["X-User-ID"]),
        project_id=UUID(headers["X-Project-ID"]),
    )


@pytest.mark.asyncio
async def test_create_reuses_campaign_conversation_and_evaluates_initial_brief(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    conversation_id = await _conversation(client, headers)

    response = await client.post(
        "/api/campaigns",
        headers=headers,
        json={
            "conversation_id": conversation_id,
            "brief": _ready_brief().model_dump(mode="json"),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["status"] == "READY"
    assert generator.briefs == []
    async with campaign_api_session_factory() as session:
        persisted = await SQLAlchemyCampaignRepository(session).find_campaign_by_conversation(
            conversation_id=UUID(conversation_id),
            scope=_scope(headers),
        )
    assert persisted is not None
    assert str(persisted.id) == body["id"]


@pytest.mark.asyncio
async def test_create_rejects_wrong_kind_and_out_of_scope_conversations(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
) -> None:
    client, generator = campaign_api
    owner = _headers()
    post_conversation_id = await _conversation(client, owner, campaign_kind=False)
    campaign_conversation_id = await _conversation(client, owner)

    wrong_kind = await client.post(
        "/api/campaigns",
        headers=owner,
        json={"conversation_id": post_conversation_id},
    )
    out_of_scope = await client.post(
        "/api/campaigns",
        headers=_headers(),
        json={"conversation_id": campaign_conversation_id},
    )

    assert wrong_kind.status_code == 404
    assert out_of_scope.status_code == 404
    assert wrong_kind.json() == out_of_scope.json() == {"detail": "Campaign not found"}
    assert generator.briefs == []


@pytest.mark.asyncio
async def test_get_campaign_reports_only_a_current_plan_as_available(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    created = await _campaign(client, headers, brief=_ready_brief())
    campaign_id = UUID(created["id"])

    detail = await client.get(f"/api/campaigns/{campaign_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["brief"] == _ready_brief().model_dump(mode="json")
    assert detail.json()["status"] == "READY"
    assert detail.json()["plan_available"] is False

    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        await repository.save_plan(
            campaign_id=campaign_id,
            scope=_scope(headers),
            plan=_plan(campaign_name="Physically persisted old plan"),
        )

    stale_detail = await client.get(f"/api/campaigns/{campaign_id}", headers=headers)
    stale_plan = await client.get(f"/api/campaigns/{campaign_id}/plan", headers=headers)
    assert stale_detail.json()["plan_available"] is False
    assert stale_plan.status_code == 404
    assert generator.briefs == []

    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        updated = await repository.update_status(
            campaign_id=campaign_id,
            scope=_scope(headers),
            status=CampaignStatus.PLAN_READY,
        )
        assert updated is not None

    current_detail = await client.get(f"/api/campaigns/{campaign_id}", headers=headers)
    current_plan = await client.get(f"/api/campaigns/{campaign_id}/plan", headers=headers)
    assert current_detail.json()["plan_available"] is True
    assert current_plan.status_code == 200
    assert current_plan.json()["campaign_name"] == "Physically persisted old plan"
    assert generator.briefs == []


@pytest.mark.asyncio
async def test_generate_and_regenerate_use_latest_brief_and_replace_current_plan(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    created = await _campaign(client, headers, brief=_ready_brief())
    campaign_id = UUID(created["id"])
    generator.responses = [_plan(campaign_name="First plan")]

    first = await client.post(f"/api/campaigns/{campaign_id}/generate", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "PLAN_READY"
    assert first.json()["plan"]["campaign_name"] == "First plan"

    latest_brief = _ready_brief(location="Tirana")
    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        updated_brief = await repository.update_brief(
            campaign_id=campaign_id,
            scope=_scope(headers),
            updates=CampaignBrief(location="Tirana"),
        )
        updated_campaign = await repository.update_status(
            campaign_id=campaign_id,
            scope=_scope(headers),
            status=CampaignStatus.READY,
        )
        assert updated_brief == latest_brief
        assert updated_campaign is not None

    generator.responses = [_plan(campaign_name="Replacement plan", location="Tirana")]
    regenerated = await client.post(
        f"/api/campaigns/{campaign_id}/generate",
        headers=headers,
    )
    current = await client.get(f"/api/campaigns/{campaign_id}/plan", headers=headers)

    assert regenerated.status_code == 200
    assert regenerated.json()["plan"]["campaign_name"] == "Replacement plan"
    assert generator.briefs[-1] == latest_brief
    assert current.json()["campaign_name"] == "Replacement plan"


@pytest.mark.asyncio
async def test_briefing_campaign_cannot_generate_and_gets_do_not_invoke_generator(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    created = await _campaign(client, headers)

    detail = await client.get(f"/api/campaigns/{created['id']}", headers=headers)
    plan = await client.get(f"/api/campaigns/{created['id']}/plan", headers=headers)
    generation = await client.post(
        f"/api/campaigns/{created['id']}/generate",
        headers=headers,
    )

    assert detail.status_code == 200
    assert detail.json()["status"] == "BRIEFING"
    assert plan.status_code == 404
    assert generation.status_code == 409
    assert generator.briefs == []


@pytest.mark.asyncio
async def test_generate_provider_and_validation_failures_are_safely_mapped(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    created = await _campaign(client, headers, brief=_ready_brief())
    generator.failure = ProviderError("secret provider response")

    provider_failure = await client.post(
        f"/api/campaigns/{created['id']}/generate",
        headers=headers,
    )

    assert provider_failure.status_code == 502
    assert "secret provider response" not in provider_failure.text

    generator.failure = None
    invalid = _plan().model_copy(update={"channels": []})
    generator.responses = [invalid]
    validation_failure = await client.post(
        f"/api/campaigns/{created['id']}/generate",
        headers=headers,
    )

    assert validation_failure.status_code == 502
    assert "channels.confirmed_missing" not in validation_failure.text
    detail = await client.get(f"/api/campaigns/{created['id']}", headers=headers)
    assert detail.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_get_campaign_hides_missing_and_out_of_scope_campaigns(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
) -> None:
    client, generator = campaign_api
    owner = _headers()
    created = await _campaign(client, owner, brief=_ready_brief())

    missing = await client.get(f"/api/campaigns/{uuid4()}", headers=owner)
    out_of_scope = await client.get(
        f"/api/campaigns/{created['id']}",
        headers=_headers(),
    )

    assert missing.status_code == 404
    assert out_of_scope.status_code == 404
    assert missing.json() == out_of_scope.json() == {"detail": "Campaign not found"}
    assert generator.briefs == []


@pytest.mark.asyncio
async def test_export_returns_exact_current_campaign_package_without_state_change(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    brief = _ready_brief()
    created = await _campaign(client, headers, brief=brief)
    campaign_id = UUID(created["id"])
    plan = _plan(campaign_name="Safe export (test) \\ ../campaign")
    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        saved = await repository.save_plan(
            campaign_id=campaign_id,
            scope=_scope(headers),
            plan=plan,
        )
        campaign = await repository.update_status(
            campaign_id=campaign_id,
            scope=_scope(headers),
            status=CampaignStatus.PLAN_READY,
        )
        assert saved == plan
        assert campaign is not None

    response = await client.get(f"/api/campaigns/{campaign_id}/export", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="campaign-export.zip"'
    )
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "campaign-plan.pdf",
            "campaign-plan.json",
            "campaign-brief.json",
        ]
        exported_plan = CampaignPlan.model_validate(
            json.loads(archive.read("campaign-plan.json"))
        )
        exported_brief = CampaignBrief.model_validate(
            json.loads(archive.read("campaign-brief.json"))
        )
        pdf = archive.read("campaign-plan.pdf")
    assert exported_plan == plan
    assert exported_brief == brief
    assert pdf.startswith(b"%PDF-")
    for section in (
        b"Executive Summary",
        b"Objective",
        b"Target Audience",
        b"Channel Strategies",
        b"Content Directions",
        b"Timeline",
        b"KPIs",
        b"Assumptions or Risks",
        b"Next Steps",
    ):
        assert section in pdf
    assert generator.briefs == []

    async with campaign_api_session_factory() as session:
        repository = SQLAlchemyCampaignRepository(session)
        persisted_campaign = await repository.get_campaign(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
        persisted_brief = await repository.get_brief(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
        persisted_plan = await repository.get_plan(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
    assert persisted_campaign is not None
    assert persisted_campaign.status is CampaignStatus.PLAN_READY
    assert persisted_brief == brief
    assert persisted_plan == plan


@pytest.mark.asyncio
async def test_export_rejects_stale_briefing_missing_and_out_of_scope_campaigns(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    owner = _headers()
    stale = await _campaign(client, owner, brief=_ready_brief())
    briefing = await _campaign(client, owner)
    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        saved = await repository.save_plan(
            campaign_id=UUID(stale["id"]),
            scope=_scope(owner),
            plan=_plan(campaign_name="Outdated plan"),
        )
        assert saved is not None

    stale_response = await client.get(
        f"/api/campaigns/{stale['id']}/export",
        headers=owner,
    )
    briefing_response = await client.get(
        f"/api/campaigns/{briefing['id']}/export",
        headers=owner,
    )
    missing_response = await client.get(f"/api/campaigns/{uuid4()}/export", headers=owner)
    out_of_scope_response = await client.get(
        f"/api/campaigns/{stale['id']}/export",
        headers=_headers(),
    )

    assert stale_response.status_code == 404
    assert stale_response.json() == {"detail": "Campaign Plan not available"}
    assert briefing_response.status_code == 404
    assert missing_response.json() == {"detail": "Campaign not found"}
    assert out_of_scope_response.json() == {"detail": "Campaign not found"}
    assert generator.briefs == []


@pytest.mark.asyncio
async def test_export_failure_is_generic_and_leaves_persisted_data_untouched(
    campaign_api: tuple[AsyncClient, FakeCampaignPlanGenerator],
    campaign_api_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, generator = campaign_api
    headers = _headers()
    brief = _ready_brief()
    plan = _plan()
    created = await _campaign(client, headers, brief=brief)
    campaign_id = UUID(created["id"])
    async with campaign_api_session_factory.begin() as session:
        repository = SQLAlchemyCampaignRepository(session)
        await repository.save_plan(
            campaign_id=campaign_id,
            scope=_scope(headers),
            plan=plan,
        )
        await repository.update_status(
            campaign_id=campaign_id,
            scope=_scope(headers),
            status=CampaignStatus.PLAN_READY,
        )

    class FailingExporter:
        def export(self, *, brief: CampaignBrief, plan: CampaignPlan) -> None:
            raise CampaignExportError("C:\\private\\temporary\\campaign.pdf")

    app.dependency_overrides[get_campaign_export_service] = FailingExporter
    response = await client.get(f"/api/campaigns/{campaign_id}/export", headers=headers)

    assert response.status_code == 500
    assert response.json() == {"detail": "Campaign export could not be created"}
    assert "private" not in response.text
    assert generator.briefs == []
    async with campaign_api_session_factory() as session:
        repository = SQLAlchemyCampaignRepository(session)
        persisted_campaign = await repository.get_campaign(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
        persisted_brief = await repository.get_brief(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
        persisted_plan = await repository.get_plan(
            campaign_id=campaign_id,
            scope=_scope(headers),
        )
    assert persisted_campaign is not None
    assert persisted_campaign.status is CampaignStatus.PLAN_READY
    assert persisted_brief == brief
    assert persisted_plan == plan


def test_campaign_export_service_is_deterministic_and_uses_no_persistent_files(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    brief = _ready_brief()
    plan = _plan()
    brief_before = brief.model_dump()
    plan_before = plan.model_dump()
    service = CampaignExportService()

    first = service.export(brief=brief, plan=plan)
    second = service.export(brief=brief, plan=plan)

    assert first == second
    assert brief.model_dump() == brief_before
    assert plan.model_dump() == plan_before
    assert list(tmp_path.iterdir()) == []


def test_campaign_pdf_preserves_multilingual_unicode_text() -> None:
    multilingual = "Fushatë për nxënësit e Kosovës. Привет, кампания."
    brief = _ready_brief()
    plan = _plan().model_copy(update={"executive_summary": multilingual})

    result = CampaignExportService().export(brief=brief, plan=plan)

    with ZipFile(BytesIO(result.content)) as archive:
        pdf = archive.read("campaign-plan.pdf")
        exported_plan = CampaignPlan.model_validate(
            json.loads(archive.read("campaign-plan.json"))
        )
    assert exported_plan.executive_summary == multilingual
    # ReportLab's embedded TrueType font records every rendered glyph in ToUnicode.
    for character in set(multilingual):
        if ord(character) > 127:
            assert f"<{ord(character):04X}>".encode() in pdf
