import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.repositories.supervisor import (
    SQLAlchemySupervisorCheckpointStore,
)
from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.client_understanding import UnderstandingField
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.exceptions import InvocationFailedError
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    InMemoryExecutionTraceRecorder,
)
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    PostSupervisor,
    SupervisorAction,
    SupervisorPlan,
    SupervisorStage,
    SupervisorStagePolicy,
)
from app.modules.posts.orchestration import (
    ClientUnderstandingStageHandler,
    PostSupervisorExecutor,
    SupervisorStageContext,
)
from app.modules.posts.providers import LLMRequest, LLMResponse, ProviderBundle
from app.modules.posts.services import PostsService


class _SequenceLLM:
    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=self._responses[index],
            provider="test",
            model="structured-understanding-test",
            input_tokens=120,
            output_tokens=80,
        )


def _provider_bundle(llm: _SequenceLLM) -> ProviderBundle:
    return ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={
            "llm": "test",
            "vision": "mock",
            "image": "mock",
            "embedding": "mock",
            "research": "mock",
            "storage": "mock",
        },
    )


def _valid_response(**overrides) -> str:
    data = {
        "business": "kafiteria",
        "brand": "LUMMA",
        "product_service": "kafiteria",
        "goal": "më shumë vizita",
        "audience": "shqiptar",
        "market": None,
        "location": "Wrong provider location",
        "platform": "Instagram",
        "language": "shqip",
        "offer": "N/A",
        "cta_intent": None,
        "style_preferences": ["warm", "minimal"],
        "constraints": ["Do not alter the logo"],
        "evidence": {
            "business": "kafiteria",
            "brand": "LUMMA",
            "product_service": "kafiteria",
            "goal": "më shumë vizita",
            "audience": "gjuhën shqipe",
            "platform": "Instagram",
            "language": "shqipe",
        },
    }
    data.update(overrides)
    return json.dumps(data)


def _context(*, attachment_id=None) -> SupervisorStageContext:
    state = empty_workflow_state()
    attachments = []
    if attachment_id is not None:
        attachments.append(
            {
                "id": str(attachment_id),
                "role": "logo",
                "original_filename": "lumma-logo.png",
                "mime_type": "image/png",
                "width": 800,
                "height": 400,
                "metadata": {"source": "client"},
            }
        )
    state[PostWorkflowSection.CONVERSATION_CONTEXT.value] = {
        "conversation_history": [
            {"role": "assistant", "content": "What would you like to promote?"}
        ],
        "latest_message": (
            "Kjo është logoja, kjo është kafiteria. Quhet LUMMA dhe dua post "
            "për Instagram në gjuhën shqipe për më shumë vizita."
        ),
        "attachments": attachments,
        "project_context": {
            "company": "LUMMA LLC",
            "brand": "LUMMA",
            "product": "Cafe experience",
            "location": "Prishtina",
            "constraints": ["Use only verified business facts"],
        },
    }
    return SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )


@pytest.mark.asyncio
async def test_client_understanding_extracts_lumma_brief_without_strategy_drift() -> None:
    attachment_id = uuid4()
    llm = _SequenceLLM(_valid_response())
    recorder = InMemoryExecutionTraceRecorder()
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(llm),
        trace_recorder=recorder,
    )

    result = await handler.execute(_context(attachment_id=attachment_id))

    assert set(result.outputs) == {PostWorkflowSection.BRIEF}
    brief = result.outputs[PostWorkflowSection.BRIEF]
    assert brief["business"] == "LUMMA LLC"
    assert brief["brand"] == "LUMMA"
    assert brief["product_service"] == "Cafe experience"
    assert brief["location"] == "Prishtina"
    assert brief["goal"] == "më shumë vizita"
    assert brief["platform"] == "Instagram"
    assert brief["language"] == "shqip"
    assert brief["audience"] is None
    assert "marketing_strategy" not in brief
    assert "creative_concept" not in brief
    assert brief["assets"] == [
        {
            "id": str(attachment_id),
            "role": "logo",
            "original_filename": "lumma-logo.png",
            "preserve_identity": True,
        }
    ]
    assert UnderstandingField.AUDIENCE.value in brief["missing_fields"]
    assert UnderstandingField.OFFER.value in brief["missing_fields"]
    assert brief["constraints"] == [
        "Use only verified business facts",
    ]
    assert llm.requests[0].response_format == "json"
    assert llm.requests[0].temperature == 0
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.PROVIDER,
        ExecutionRunKind.AGENT,
    ]
    assert all(trace.status is ExecutionRunStatus.SUCCEEDED for trace in recorder.traces)
    assert "Kjo është logoja" not in repr(recorder.traces)


@pytest.mark.asyncio
async def test_client_understanding_retries_and_rejects_strategy_fields() -> None:
    invalid = json.loads(_valid_response())
    invalid["marketing_strategy"] = {"positioning": "invented"}
    llm = _SequenceLLM(json.dumps(invalid))
    handler = ClientUnderstandingStageHandler(_provider_bundle(llm))

    with pytest.raises(InvocationFailedError) as failure:
        await handler.execute(_context())

    assert failure.value.attempts == 2
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_client_understanding_rejects_invalid_provider_json() -> None:
    llm = _SequenceLLM("not-json")
    handler = ClientUnderstandingStageHandler(_provider_bundle(llm))

    with pytest.raises(InvocationFailedError):
        await handler.execute(_context())

    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_client_understanding_routes_an_outcome_to_goal_not_cta() -> None:
    response = json.loads(_valid_response())
    response["goal"] = None
    response["cta_intent"] = "më shumë vizita"
    response["evidence"].pop("goal")
    response["evidence"]["cta_intent"] = "më shumë vizita"
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(_SequenceLLM(json.dumps(response)))
    )

    result = await handler.execute(_context())

    assert result.outputs[PostWorkflowSection.BRIEF]["goal"] == "më shumë vizita"
    assert result.outputs[PostWorkflowSection.BRIEF]["cta_intent"] is None


@pytest.mark.asyncio
async def test_client_understanding_keeps_an_explicit_cta_action() -> None:
    context = _context()
    context.workflow_state["conversation_context"]["latest_message"] += " Rezervo tani."
    response = json.loads(_valid_response())
    response["cta_intent"] = "Rezervo tani"
    response["evidence"]["cta_intent"] = "Rezervo tani"
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(_SequenceLLM(json.dumps(response)))
    )

    result = await handler.execute(context)

    assert result.outputs[PostWorkflowSection.BRIEF]["cta_intent"] == "Rezervo tani"


@pytest.mark.asyncio
async def test_client_understanding_uses_only_high_confidence_factual_fallbacks() -> None:
    context = _context()
    context.workflow_state["conversation_context"]["project_context"] = {}
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(_SequenceLLM(json.dumps({})))
    )

    result = await handler.execute(context)
    brief = result.outputs[PostWorkflowSection.BRIEF]

    assert brief["business"] == "kafiteria"
    assert brief["brand"] == "LUMMA"
    assert brief["product_service"] == "kafiteria"
    assert brief["goal"] == "më shumë vizita"
    assert brief["platform"] == "Instagram"
    assert brief["language"] == "shqip"
    assert brief["audience"] is None
    assert brief["market"] is None
    assert brief["offer"] is None
    assert brief["cta_intent"] is None


@pytest.mark.asyncio
async def test_client_understanding_normalizes_goal_and_recovers_explicit_cta() -> None:
    context = _context()
    context.workflow_state["conversation_context"]["project_context"] = {}
    context.workflow_state["conversation_context"]["latest_message"] = (
        "Biznesi im eshte studio arkitekture. Brandi quhet ARKA. Sherbimi eshte "
        "projektim arkitekturor. Dua me shume kliente ne LinkedIn. Kontakto tani."
    )
    response = {
        "business": "studio arkitekture",
        "brand": "ARKA",
        "product_service": "projektim arkitekturor",
        "goal": "Dua me shume kliente ne LinkedIn",
        "platform": "LinkedIn",
        "evidence": {
            "business": "studio arkitekture",
            "brand": "ARKA",
            "product_service": "projektim arkitekturor",
            "goal": "Dua me shume kliente ne LinkedIn",
            "platform": "LinkedIn",
        },
    }
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(_SequenceLLM(json.dumps(response)))
    )

    result = await handler.execute(context)
    brief = result.outputs[PostWorkflowSection.BRIEF]

    assert brief["business"] == "studio arkitekture"
    assert brief["goal"] == "me shume kliente"
    assert brief["cta_intent"] == "Kontakto tani"


def test_supervisor_requires_conversation_context_before_understanding() -> None:
    decision = PostSupervisor().decide(empty_workflow_state())

    assert decision.action is SupervisorAction.STOP
    assert decision.next_stage is SupervisorStage.CLIENT_UNDERSTANDING
    assert decision.required_inputs == (PostWorkflowSection.CONVERSATION_CONTEXT.value,)


@pytest.mark.asyncio
async def test_understanding_input_rejects_duplicate_attachment_identity() -> None:
    attachment_id = uuid4()
    context = _context(attachment_id=attachment_id)
    attachment = context.workflow_state["conversation_context"]["attachments"][0]
    context.workflow_state["conversation_context"]["attachments"].append(dict(attachment))
    handler = ClientUnderstandingStageHandler(_provider_bundle(_SequenceLLM(_valid_response())))

    with pytest.raises(ValueError, match="attachment IDs must be unique"):
        await handler.execute(context)


@pytest_asyncio.fixture
async def understanding_session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_supervisor_executes_understanding_and_persists_only_brief(
    understanding_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with understanding_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="LUMMA understanding",
        )
        generation = await service.request_generation(post_id=post.id, scope=scope)
        state = await service.write_workflow_section(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
            section=PostWorkflowSection.CONVERSATION_CONTEXT,
            value=_context().workflow_state["conversation_context"],
            expected_version=1,
        )
        await service.write_workflow_section(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
            section=PostWorkflowSection.QUALITY,
            value={"decision": "PASS"},
            expected_version=state.version,
        )

    recorder = InMemoryExecutionTraceRecorder()
    handler = ClientUnderstandingStageHandler(
        _provider_bundle(_SequenceLLM(_valid_response())),
        trace_recorder=recorder,
    )
    plan = SupervisorPlan(
        (
            SupervisorStagePolicy(
                SupervisorStage.CLIENT_UNDERSTANDING,
                required_sections=(PostWorkflowSection.CONVERSATION_CONTEXT,),
                output_sections=(PostWorkflowSection.BRIEF,),
            ),
        )
    )
    executor = PostSupervisorExecutor(
        store=SQLAlchemySupervisorCheckpointStore(understanding_session_factory),
        handlers={SupervisorStage.CLIENT_UNDERSTANDING: handler},
        supervisor=PostSupervisor(plan),
        trace_recorder=recorder,
    )

    await executor.execute(generation_id=generation.id, job_id=generation.job_id)

    async with understanding_session_factory() as session:
        persisted = await PostsService(SQLAlchemyPostRepository(session)).get_workflow_state(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
        )
    assert persisted.data["brief"]["brand"] == "LUMMA"
    assert persisted.data["marketing_strategy"] == {}
    assert persisted.data["creative_concept"] == {}
    assert persisted.data["supervisor"]["completed_stages"] == [
        SupervisorStage.CLIENT_UNDERSTANDING.value
    ]
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.PROVIDER,
        ExecutionRunKind.AGENT,
        ExecutionRunKind.GENERATION_STEP,
    ]
