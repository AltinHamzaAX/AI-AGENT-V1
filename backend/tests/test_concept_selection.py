from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_creative_director_agent import _CreativeLLM, _input, _run

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.modules.posts.agents.creative_director import CONCEPT_SELECTION_DIMENSIONS
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.memory import (
    SemanticMemoryKind,
    SemanticMemoryScope,
    SemanticMemoryScopeLevel,
)
from app.modules.posts.domain.supervisor import SupervisorAction
from app.modules.posts.orchestration.creative_direction import CreativeDirectionStageHandler
from app.modules.posts.orchestration.supervisor import SupervisorStageContext
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.services.concept_memory import ConceptMemoryService


class _SemanticMemorySpy:
    def __init__(self) -> None:
        self.stored: list[dict[str, object]] = []
        self.retrieved: list[dict[str, object]] = []

    async def store(self, **values: object) -> object:
        self.stored.append(values)
        return object()

    async def retrieve(self, **values: object) -> tuple[object, ...]:
        self.retrieved.append(values)
        return (
            SimpleNamespace(
                memory=SimpleNamespace(content="Rejected route: generic floating product.")
            ),
        )


class _ScopeResolver:
    def __init__(self, scope: SemanticMemoryScope) -> None:
        self.scope = scope

    async def resolve_project_scope(self, *, post_id: object) -> SemanticMemoryScope:
        del post_id
        return self.scope


def _scope() -> SemanticMemoryScope:
    return SemanticMemoryScope(
        user_id=uuid4(),
        level=SemanticMemoryScopeLevel.PROJECT,
        project_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_exact_ticket_dimensions_rank_three_candidates_and_publish_rejections() -> None:
    result = await _run(await _input(), _CreativeLLM())

    assert tuple(result.big_idea_candidates[0].evaluation.selection_scores()) == (
        CONCEPT_SELECTION_DIMENSIONS
    )
    assert result.winning_concept.candidate_id == "idea_2"
    assert result.winning_concept.total_score == 72
    assert [item.candidate_id for item in result.rejected_concepts] == ["idea_3", "idea_1"]
    assert all(item.rejection_reason for item in result.rejected_concepts)


@pytest.mark.asyncio
async def test_prior_rejections_reach_generation_as_anti_repetition_context() -> None:
    payload = (await _input()).model_copy(
        update={"rejected_concept_memory": ["Do not repeat the generic floating product route."]}
    )
    llm = _CreativeLLM()

    await _run(payload, llm)

    request = llm.requests[0].messages[-1].content
    assert "Do not repeat the generic floating product route." in request


@pytest.mark.asyncio
async def test_rejected_candidates_are_stored_as_project_scoped_semantic_memory() -> None:
    semantic_memory = _SemanticMemorySpy()
    service = ConceptMemoryService(semantic_memory)  # type: ignore[arg-type]
    direction = await _run(await _input(), _CreativeLLM())
    generation_id = uuid4()
    scope = _scope()

    await service.remember_rejected(
        scope=scope,
        direction=direction,
        generation_id=generation_id,
    )

    assert len(semantic_memory.stored) == 2
    for stored, rejected in zip(
        semantic_memory.stored,
        direction.rejected_concepts,
        strict=True,
    ):
        assert stored["scope"] == scope
        assert stored["kind"] is SemanticMemoryKind.REJECTED_CONCEPT
        assert rejected.candidate_id not in str(stored["content"])
        metadata = stored["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["candidate_id"] == rejected.candidate_id
        assert metadata["generation_id"] == str(generation_id)
        assert tuple(metadata["selection_scores"]) == CONCEPT_SELECTION_DIMENSIONS


@pytest.mark.asyncio
async def test_recall_reads_only_rejected_concepts() -> None:
    semantic_memory = _SemanticMemorySpy()
    service = ConceptMemoryService(semantic_memory)  # type: ignore[arg-type]
    scope = _scope()

    recalled = await service.recall_rejected(scope=scope, query="premium airport arrival")

    assert recalled == ("Rejected route: generic floating product.",)
    assert semantic_memory.retrieved == [
        {
            "scope": scope,
            "query": "premium airport arrival",
            "kinds": (SemanticMemoryKind.REJECTED_CONCEPT,),
            "limit": 10,
            "min_similarity": 0.2,
        }
    ]


@pytest.mark.asyncio
async def test_stage_recalls_before_generation_and_persists_every_loser() -> None:
    payload = await _input()
    llm = _CreativeLLM()
    semantic_memory = _SemanticMemorySpy()
    concept_memory = ConceptMemoryService(semantic_memory)  # type: ignore[arg-type]
    scope = _scope()
    providers = ProviderBundle(
        llm=llm,
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        creative_llm_override=llm,
    )
    state = {
        PostWorkflowSection.MARKETING_STRATEGY.value: payload.marketing_strategy.model_dump(
            mode="json"
        ),
        PostWorkflowSection.AUDIENCE.value: payload.audience.model_dump(mode="json"),
        PostWorkflowSection.BRAND.value: payload.brand.model_dump(mode="json"),
        PostWorkflowSection.RESEARCH.value: payload.research.model_dump(mode="json"),
        PostWorkflowSection.SEMANTIC_CONTRACT.value: payload.semantic_contract,
    }
    context = SupervisorStageContext(
        generation_id=uuid4(),
        post_id=uuid4(),
        job_id=uuid4(),
        workflow_state=state,
        state_version=1,
        action=SupervisorAction.CONTINUE,
    )

    result = await CreativeDirectionStageHandler(
        providers,
        concept_memory=concept_memory,
        memory_scope_resolver=_ScopeResolver(scope),
    ).execute(context)

    output = result.outputs[PostWorkflowSection.CREATIVE_CONCEPT]
    assert output["winning_concept"]["candidate_id"] == "idea_2"
    assert "Rejected route: generic floating product." in llm.requests[0].messages[-1].content
    assert len(semantic_memory.stored) == 2
