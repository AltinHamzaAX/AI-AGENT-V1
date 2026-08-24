import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.posts.domain.enums import GenerationStatus, PostWorkflowSection
from app.modules.posts.domain.jobs import NonRetryableJobError
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.domain.state import PostGenerationState, validate_section_value
from app.modules.posts.domain.supervisor import (
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SupervisorCheckpoint:
    generation_id: UUID
    post_id: UUID
    status: GenerationStatus
    state: PostGenerationState


@dataclass(frozen=True, slots=True)
class SupervisorStageContext:
    generation_id: UUID
    post_id: UUID
    job_id: UUID
    workflow_state: dict[str, Any]
    state_version: int
    action: SupervisorAction


@dataclass(frozen=True, slots=True)
class SupervisorStageResult:
    outputs: dict[PostWorkflowSection, Any]


class SupervisorStageHandler(Protocol):
    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult: ...


class SupervisorCheckpointStore(Protocol):
    async def load(self, *, generation_id: UUID) -> SupervisorCheckpoint | None: ...

    async def write_section(
        self,
        *,
        generation_id: UUID,
        section: PostWorkflowSection,
        value: Any,
        expected_version: int,
    ) -> PostGenerationState | None: ...

    async def set_generation_status(
        self,
        *,
        generation_id: UUID,
        status: GenerationStatus,
    ) -> bool: ...


class SupervisorBlockedError(NonRetryableJobError):
    """The workflow cannot proceed until a declared requirement is provided."""


class PostSupervisorExecutor:
    """GenerationExecutor that resumes a persisted supervisor state machine."""

    def __init__(
        self,
        *,
        store: SupervisorCheckpointStore,
        handlers: dict[SupervisorStage, SupervisorStageHandler],
        supervisor: PostSupervisor | None = None,
        max_decisions: int = 100,
    ) -> None:
        if not 1 <= max_decisions <= 1000:
            raise ValueError("max_decisions must be between 1 and 1000")
        self._store = store
        self._handlers = dict(handlers)
        self._supervisor = supervisor or PostSupervisor()
        self._max_decisions = max_decisions

    async def execute(self, *, generation_id: UUID, job_id: UUID) -> None:
        if not await self._store.set_generation_status(
            generation_id=generation_id,
            status=GenerationStatus.RUNNING,
        ):
            raise NonRetryableJobError("generation not found")

        for _ in range(self._max_decisions):
            checkpoint = await self._store.load(generation_id=generation_id)
            if checkpoint is None:
                raise NonRetryableJobError("generation checkpoint not found")
            decision = self._supervisor.decide(
                checkpoint.state.data,
                available_stages=frozenset(self._handlers),
            )
            state = self._supervisor.record_decision(checkpoint.state.data, decision)
            persisted = await self._store.write_section(
                generation_id=generation_id,
                section=PostWorkflowSection.SUPERVISOR,
                value=state[PostWorkflowSection.SUPERVISOR.value],
                expected_version=checkpoint.state.version,
            )
            if persisted is None:
                continue
            logger.info(
                "posts.supervisor.decision",
                extra={
                    "event": "posts.supervisor.decision",
                    "generation_id": str(generation_id),
                    "post_id": str(checkpoint.post_id),
                    "job_id": str(job_id),
                    "action": decision.action.value,
                    "next_stage": (
                        decision.next_stage.value if decision.next_stage else None
                    ),
                    "reason": decision.reason,
                },
            )
            if decision.action is SupervisorAction.SKIP:
                continue
            if decision.action is SupervisorAction.STOP:
                if decision.reason == "workflow complete and quality approved":
                    return
                if decision.terminal:
                    raise NonRetryableJobError(decision.reason)
                raise SupervisorBlockedError(decision.reason)

            stage = decision.next_stage
            if stage is None:
                raise NonRetryableJobError("supervisor omitted next_stage")
            result = await self._handlers[stage].execute(
                SupervisorStageContext(
                    generation_id=generation_id,
                    post_id=checkpoint.post_id,
                    job_id=job_id,
                    workflow_state=persisted.data,
                    state_version=persisted.version,
                    action=decision.action,
                )
            )
            persisted = await self._persist_stage_outputs(
                generation_id=generation_id,
                stage=stage,
                result=result,
                state=persisted,
            )
            completed = self._supervisor.mark_stage_completed(persisted.data, stage)
            revision_history = completed[PostWorkflowSection.REVISION_HISTORY.value]
            if revision_history != persisted.data[PostWorkflowSection.REVISION_HISTORY.value]:
                updated = await self._store.write_section(
                    generation_id=generation_id,
                    section=PostWorkflowSection.REVISION_HISTORY,
                    value=revision_history,
                    expected_version=persisted.version,
                )
                if updated is None:
                    raise RuntimeError("workflow state changed during revision persistence")
                persisted = updated
            saved = await self._store.write_section(
                generation_id=generation_id,
                section=PostWorkflowSection.SUPERVISOR,
                value=completed[PostWorkflowSection.SUPERVISOR.value],
                expected_version=persisted.version,
            )
            if saved is None:
                continue
        raise RuntimeError("supervisor decision limit exceeded")

    async def _persist_stage_outputs(
        self,
        *,
        generation_id: UUID,
        stage: SupervisorStage,
        result: SupervisorStageResult,
        state: PostGenerationState,
    ) -> PostGenerationState:
        policy = self._supervisor.policy(stage)
        if set(result.outputs) != set(policy.output_sections):
            raise NonRetryableJobError("stage returned unexpected workflow sections")
        current = state
        for section in policy.output_sections:
            value = _validate_stage_output(section, result.outputs[section])
            if (
                section is PostWorkflowSection.SEMANTIC_CONTRACT
                and current.data[section.value]
                and current.data[section.value] != value
            ):
                raise NonRetryableJobError("semantic contract is immutable")
            updated = await self._store.write_section(
                generation_id=generation_id,
                section=section,
                value=value,
                expected_version=current.version,
            )
            if updated is None:
                raise RuntimeError("workflow state changed during stage persistence")
            current = updated
        return current


def _validate_stage_output(section: PostWorkflowSection, value: Any) -> Any:
    validated = validate_section_value(section, value)
    if section is PostWorkflowSection.SEMANTIC_CONTRACT:
        try:
            PostSemanticContract.from_dict(validated)
        except (KeyError, TypeError, ValueError) as exc:
            raise NonRetryableJobError("stage returned invalid semantic contract") from exc
    return validated


__all__ = [
    "PostSupervisorExecutor",
    "SupervisorBlockedError",
    "SupervisorCheckpoint",
    "SupervisorCheckpointStore",
    "SupervisorStageContext",
    "SupervisorStageHandler",
    "SupervisorStageResult",
]
