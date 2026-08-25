from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.repositories.supervisor import (
    SQLAlchemySupervisorCheckpointStore,
)
from app.models.posts import PostGenerationJobModel, PostGenerationModel
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import GenerationStatus, PostWorkflowSection
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    InMemoryExecutionTraceRecorder,
)
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import (
    PostGenerationState,
    empty_workflow_state,
    validate_section_value,
)
from app.modules.posts.domain.supervisor import (
    PostSupervisor,
    SupervisorAction,
    SupervisorPlan,
    SupervisorStage,
    SupervisorStagePolicy,
)
from app.modules.posts.orchestration import (
    PostSupervisorExecutor,
    SupervisorCheckpoint,
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.services import PostsService
from app.workers.generation_worker import GenerationWorker


def _small_plan(*, max_attempts: int = 3) -> SupervisorPlan:
    return SupervisorPlan(
        (
            SupervisorStagePolicy(
                SupervisorStage.CLIENT_UNDERSTANDING,
                output_sections=(PostWorkflowSection.BRIEF,),
                max_attempts=max_attempts,
            ),
            SupervisorStagePolicy(
                SupervisorStage.SEMANTIC_CONTRACT,
                dependencies=(SupervisorStage.CLIENT_UNDERSTANDING,),
                required_sections=(PostWorkflowSection.BRIEF,),
                output_sections=(PostWorkflowSection.SEMANTIC_CONTRACT,),
            ),
        )
    )


def _state() -> dict:
    state = empty_workflow_state()
    state["quality"] = {"decision": "PASS"}
    return state


def _semantic_contract() -> dict:
    return PostSemanticContract.create(
        company="Promotiva",
        brand="Promotiva",
        product="Posts",
        primary_entity="Promotiva Posts",
        goal="bookings",
        audience="business owners",
        market="Kosovo",
        location="Prishtina",
        offer=None,
        cta_intent="Book now",
        platform="Instagram",
        language="Albanian",
        required_facts={},
        forbidden_claims=[],
        required_assets=[],
        constraints=[],
    ).to_dict()


def test_supervisor_routes_skips_and_checks_dependencies_from_state() -> None:
    supervisor = PostSupervisor(_small_plan())
    state = _state()

    first = supervisor.decide(state)
    assert first.action is SupervisorAction.CONTINUE
    assert first.next_stage is SupervisorStage.CLIENT_UNDERSTANDING

    state["brief"] = {"goal": "bookings"}
    skipped = supervisor.decide(state)
    assert skipped.action is SupervisorAction.SKIP
    state = supervisor.record_decision(state, skipped)

    semantic = supervisor.decide(state)
    assert semantic.action is SupervisorAction.CONTINUE
    assert semantic.next_stage is SupervisorStage.SEMANTIC_CONTRACT
    assert semantic.state_requirements == (PostWorkflowSection.BRIEF,)


def test_supervisor_stops_for_missing_inputs_and_unregistered_handler() -> None:
    supervisor = PostSupervisor(_small_plan())
    state = _state()
    progress = {
        "current_stage": None,
        "completed_stages": [SupervisorStage.CLIENT_UNDERSTANDING.value],
        "skipped_stages": [],
        "invalidated_stages": [],
        "requested_skips": [],
        "stage_attempts": {},
        "last_decision": {},
    }
    state["supervisor"] = progress

    missing = supervisor.decide(state)
    assert missing.action is SupervisorAction.STOP
    assert missing.required_inputs == ("brief",)
    assert missing.terminal is False

    state["brief"] = {"goal": "bookings"}
    unavailable = supervisor.decide(state, available_stages=frozenset())
    assert unavailable.action is SupervisorAction.STOP
    assert unavailable.required_inputs == ("stage_handler:semantic_contract",)


def test_supervisor_retries_then_stops_at_stage_limit() -> None:
    supervisor = PostSupervisor(_small_plan(max_attempts=2))
    state = _state()

    first = supervisor.decide(state)
    state = supervisor.record_decision(state, first)
    retry = supervisor.decide(state)
    assert retry.action is SupervisorAction.RETRY

    state = supervisor.record_decision(state, retry)
    exhausted = supervisor.decide(state)
    assert exhausted.action is SupervisorAction.STOP
    assert exhausted.terminal is True
    assert exhausted.reason == "stage retry limit exhausted"


def test_targeted_revision_and_hard_gate_override_normal_routing() -> None:
    supervisor = PostSupervisor(_small_plan())
    state = _state()
    state["brief"] = {"goal": "bookings"}
    state["semantic_contract"] = _semantic_contract()
    state["revision_history"] = [
        {
            "status": "pending",
            "target_stage": SupervisorStage.CLIENT_UNDERSTANDING.value,
            "keep": ["brief"],
            "change": ["brief"],
        }
    ]

    revision = supervisor.decide(state)
    assert revision.action is SupervisorAction.REVISE
    assert revision.next_stage is SupervisorStage.CLIENT_UNDERSTANDING
    state = supervisor.record_decision(state, revision)
    assert state["supervisor"]["invalidated_stages"] == [
        "client_understanding",
        "semantic_contract",
    ]
    state = supervisor.mark_stage_completed(state, SupervisorStage.CLIENT_UNDERSTANDING)
    recompute = supervisor.decide(state)
    assert recompute.action is SupervisorAction.CONTINUE
    assert recompute.next_stage is SupervisorStage.SEMANTIC_CONTRACT

    state["quality"] = {"hard_fail": True, "decision": "BLOCKED"}
    stopped = supervisor.decide(state)
    assert stopped.action is SupervisorAction.STOP
    assert stopped.terminal is True
    assert stopped.next_stage is None


def test_semantic_contract_revision_is_a_terminal_hard_fail() -> None:
    supervisor = PostSupervisor(_small_plan())
    state = _state()
    state["brief"] = {"goal": "bookings"}
    state["revision_history"] = [
        {
            "status": "pending",
            "target_stage": SupervisorStage.SEMANTIC_CONTRACT.value,
        }
    ]

    decision = supervisor.decide(state)

    assert decision.action is SupervisorAction.STOP
    assert decision.terminal is True
    assert decision.reason == "semantic contract is immutable and cannot be revised"


def test_supervisor_plan_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        SupervisorPlan(
            (
                SupervisorStagePolicy(
                    SupervisorStage.CLIENT_UNDERSTANDING,
                    dependencies=(SupervisorStage.SEMANTIC_CONTRACT,),
                ),
                SupervisorStagePolicy(
                    SupervisorStage.SEMANTIC_CONTRACT,
                    dependencies=(SupervisorStage.CLIENT_UNDERSTANDING,),
                ),
            )
        )


class _InMemoryStore:
    def __init__(self, state: dict) -> None:
        now = datetime.now(UTC)
        self.post_id = uuid4()
        self.status = GenerationStatus.QUEUED
        self.state = PostGenerationState(
            generation_id=uuid4(),
            schema_version=2,
            version=1,
            data=deepcopy(state),
            created_at=now,
            updated_at=now,
        )

    async def load(self, *, generation_id: UUID) -> SupervisorCheckpoint | None:
        if generation_id != self.state.generation_id:
            return None
        return SupervisorCheckpoint(
            generation_id=generation_id,
            post_id=self.post_id,
            status=self.status,
            state=self.state,
        )

    async def write_section(
        self,
        *,
        generation_id: UUID,
        section: PostWorkflowSection,
        value: object,
        expected_version: int,
    ) -> PostGenerationState | None:
        if generation_id != self.state.generation_id or expected_version != self.state.version:
            return None
        data = deepcopy(self.state.data)
        data[section.value] = validate_section_value(section, value)
        self.state = PostGenerationState(
            generation_id=generation_id,
            schema_version=2,
            version=expected_version + 1,
            data=data,
            created_at=self.state.created_at,
            updated_at=datetime.now(UTC),
        )
        return self.state

    async def set_generation_status(
        self,
        *,
        generation_id: UUID,
        status: GenerationStatus,
    ) -> bool:
        if generation_id != self.state.generation_id:
            return False
        self.status = status
        return True


class _StageHandler:
    def __init__(self, outputs: dict[PostWorkflowSection, object]) -> None:
        self.outputs = outputs
        self.actions: list[SupervisorAction] = []

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        self.actions.append(context.action)
        return SupervisorStageResult(outputs=self.outputs)


@pytest.mark.asyncio
async def test_executor_runs_registered_graph_and_persists_resumable_progress() -> None:
    store = _InMemoryStore(_state())
    recorder = InMemoryExecutionTraceRecorder()
    understanding = _StageHandler({PostWorkflowSection.BRIEF: {"goal": "bookings"}})
    contract = _StageHandler({PostWorkflowSection.SEMANTIC_CONTRACT: _semantic_contract()})
    executor = PostSupervisorExecutor(
        store=store,
        supervisor=PostSupervisor(_small_plan()),
        handlers={
            SupervisorStage.CLIENT_UNDERSTANDING: understanding,
            SupervisorStage.SEMANTIC_CONTRACT: contract,
        },
        trace_recorder=recorder,
    )

    await executor.execute(generation_id=store.state.generation_id, job_id=uuid4())

    progress = store.state.data["supervisor"]
    assert progress["completed_stages"] == [
        "client_understanding",
        "semantic_contract",
    ]
    assert progress["stage_attempts"] == {
        "client_understanding": 1,
        "semantic_contract": 1,
    }
    assert understanding.actions == [SupervisorAction.CONTINUE]
    assert contract.actions == [SupervisorAction.CONTINUE]
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.GENERATION_STEP,
        ExecutionRunKind.GENERATION_STEP,
    ]
    assert [trace.name for trace in recorder.traces] == [
        "client_understanding",
        "semantic_contract",
    ]


@pytest_asyncio.fixture
async def supervisor_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


@pytest.mark.asyncio
async def test_generation_worker_executes_supervisor_and_preserves_checkpoint(
    supervisor_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope = PostScope(user_id=uuid4(), project_id=uuid4())
    async with supervisor_session_factory.begin() as session:
        service = PostsService(SQLAlchemyPostRepository(session))
        post = await service.create_post(
            scope=scope,
            conversation_id=None,
            campaign_id=None,
            title="Supervisor integration",
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
            section=PostWorkflowSection.QUALITY,
            value={"decision": "PASS"},
            expected_version=state.version,
        )

    executor = PostSupervisorExecutor(
        store=SQLAlchemySupervisorCheckpointStore(supervisor_session_factory),
        supervisor=PostSupervisor(_small_plan()),
        handlers={
            SupervisorStage.CLIENT_UNDERSTANDING: _StageHandler(
                {PostWorkflowSection.BRIEF: {"goal": "bookings"}}
            ),
            SupervisorStage.SEMANTIC_CONTRACT: _StageHandler(
                {PostWorkflowSection.SEMANTIC_CONTRACT: _semantic_contract()}
            ),
        },
    )
    worker = GenerationWorker(
        session_factory=supervisor_session_factory,
        executor=executor,
        lease_seconds=30,
        retry_backoff_seconds=0,
        poll_seconds=0.01,
        worker_id="worker-supervisor",
    )

    assert await worker.run_once() is True
    async with supervisor_session_factory() as session:
        job = await session.get(PostGenerationJobModel, generation.job_id)
        generation_model = await session.get(PostGenerationModel, generation.id)
        state = await SQLAlchemyPostRepository(session).get_workflow_state(
            generation_id=generation.id,
            post_id=post.id,
            scope=scope,
        )
        assert job is not None and job.status == "completed"
        assert generation_model is not None and generation_model.status == "completed"
        assert state is not None
        assert state.data["supervisor"]["completed_stages"] == [
            "client_understanding",
            "semantic_contract",
        ]
