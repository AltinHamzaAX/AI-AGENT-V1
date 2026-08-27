import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.models.posts import PostGenerationJobModel
from app.modules.posts.agents.client_understanding import ClientUnderstandingBrief
from app.modules.posts.domain.clarification import ClarificationEngine
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import PostWorkflowSection, UnderstandingField
from app.modules.posts.domain.supervisor import DEFAULT_SUPERVISOR_PLAN, SupervisorStage
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderBundle
from app.modules.posts.services import PostsService
from app.shared.conversations.domain import ConversationScope, MessageRole
from app.workers.generation_worker import GenerationWorker
from app.workers.pipeline import build_generation_executor, build_stage_handlers

#: Every planned stage is registered. A stage added to the plan without a
#: handler fails this file rather than surfacing as a stalled generation.
UNIMPLEMENTED_STAGES: frozenset[SupervisorStage] = frozenset()


class _ScriptedLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text=self.response, provider="test", model="pipeline-test")


def _brief_response() -> str:
    return json.dumps(
        {
            "business": "car rental",
            "brand": "AtomX Rent",
            "product_service": "Skoda Fabia",
            "goal": "more bookings",
            "audience": "Albanian diaspora",
            "market": None,
            "location": "Prishtina",
            "platform": "Instagram",
            "language": "english",
            "offer": None,
            "cta_intent": "book now",
            "style_preferences": [],
            "constraints": [],
            "evidence": {
                "business": "car rental",
                "brand": "AtomX Rent",
                "product_service": "Skoda Fabia",
                "goal": "more bookings",
                "audience": "Albanian diaspora",
                "location": "Prishtina",
                "platform": "Instagram",
                "cta_intent": "book now",
            },
        }
    )


def _providers(llm: Any) -> ProviderBundle:
    return ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={"llm": "test"},
    )


@pytest_asyncio.fixture
async def pipeline_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


def test_every_planned_stage_has_a_handler_or_is_declared_unimplemented() -> None:
    handlers = build_stage_handlers(
        async_sessionmaker(),  # type: ignore[call-overload]
        _providers(_ScriptedLLM("{}")),
    )
    planned = {policy.stage for policy in DEFAULT_SUPERVISOR_PLAN.stages}

    assert set(handlers) <= planned
    assert planned - set(handlers) == UNIMPLEMENTED_STAGES
    assert all(callable(getattr(handler, "execute", None)) for handler in handlers.values())


@pytest.mark.asyncio
async def test_the_worker_claims_a_queued_job_and_runs_the_first_stage(
    pipeline_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    generation, post_id = await _queued_generation(pipeline_session_factory, scope)
    llm = _ScriptedLLM(_brief_response())
    worker = GenerationWorker(
        session_factory=pipeline_session_factory,
        executor=build_generation_executor(pipeline_session_factory, _providers(llm)),
        lease_seconds=60,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-pipeline-test",
    )

    assert await worker.run_once() is True

    async with pipeline_session_factory() as session:
        state = await SQLAlchemyPostRepository(session).get_workflow_state(
            generation_id=generation.id,
            post_id=post_id,
            scope=scope,
        )
        job = await session.get(PostGenerationJobModel, generation.job_id)

    assert llm.requests, "the wired executor never reached a specialist stage"
    assert state is not None
    assert state.data[PostWorkflowSection.BRIEF.value]["product_service"] == "Skoda Fabia"
    supervisor = state.data[PostWorkflowSection.SUPERVISOR.value]
    assert SupervisorStage.CLIENT_UNDERSTANDING.value in supervisor["completed_stages"]
    assert SupervisorStage.SEMANTIC_CONTRACT.value in supervisor["completed_stages"]
    # The contract froze exactly what the client stated, nothing more.
    contract = state.data[PostWorkflowSection.SEMANTIC_CONTRACT.value]
    assert contract["primary_entity"] == "Skoda Fabia"
    assert contract["goal"] == "more bookings"
    assert contract["audience"] == "Albanian diaspora"
    assert contract["cta_intent"] == "book now"
    assert contract["platform"] == "Instagram"
    assert job is not None


@pytest.mark.asyncio
async def test_an_idle_worker_reports_that_it_claimed_nothing(
    pipeline_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker = GenerationWorker(
        session_factory=pipeline_session_factory,
        executor=build_generation_executor(
            pipeline_session_factory,
            _providers(_ScriptedLLM("{}")),
        ),
        lease_seconds=60,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-idle-test",
    )

    assert await worker.run_once() is False


async def _queued_generation(
    session_factory: async_sessionmaker[AsyncSession],
    scope: PostScope,
) -> tuple[Any, Any]:
    conversation_scope = ConversationScope(user_id=scope.user_id, project_id=scope.project_id)
    async with session_factory.begin() as session:
        conversations = SQLAlchemyConversationRepository(session)
        conversation = await conversations.create(scope=conversation_scope, title="Pipeline")
        await conversations.append_message(
            conversation_id=conversation.id,
            scope=conversation_scope,
            role=MessageRole.USER,
            content="I run AtomX Rent in Prishtina and want more bookings for the Skoda Fabia.",
            metadata={},
        )
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=conversation.id,
            campaign_id=None,
            title="Pipeline post",
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)
        state = await service.get_workflow_state(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
        )
        await service.write_workflow_section(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
            section=PostWorkflowSection.CONVERSATION_CONTEXT,
            value={
                "conversation_history": [],
                "latest_message": (
                    "I run AtomX Rent in Prishtina and want more bookings for the "
                    "Skoda Fabia on Instagram. It is for the Albanian diaspora and "
                    "they should book now."
                ),
                "attachments": [],
                "project_context": {},
            },
            expected_version=state.version,
        )
    return generation, post.id


def test_the_brief_a_stage_writes_is_readable_by_the_stage_that_consumes_it() -> None:
    """The section shape is a contract between stages, not a local convention."""
    brief = ClientUnderstandingBrief(
        business="car rental",
        product_service="Skoda Fabia",
        goal="more bookings",
        audience="Albanian diaspora",
        cta_intent="book now",
        missing_fields=[UnderstandingField.OFFER],
    )
    written = brief.model_copy(
        update={"clarification": ClarificationEngine().evaluate(brief)}
    ).model_dump(mode="json")

    # What marketing strategy does with the persisted section.
    read_back = ClientUnderstandingBrief.model_validate(written)

    assert read_back.goal == "more bookings"
    assert read_back.clarification is not None
    assert read_back.clarification.requires_user_input is False
