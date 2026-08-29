from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.integrations.mock import MockEmbeddingProvider
from app.modules.posts.domain.memory import (
    SemanticMemory,
    SemanticMemoryKind,
    SemanticMemoryMatch,
    SemanticMemoryScope,
    SemanticMemoryScopeLevel,
)
from app.modules.posts.services import SemanticMemoryService


class InMemorySemanticMemoryRepository:
    embedding_dimension = 8

    def __init__(self) -> None:
        self.records: dict[
            tuple[SemanticMemoryScope, SemanticMemoryKind, str], SemanticMemory
        ] = {}

    async def upsert(
        self,
        *,
        memory_id: UUID,
        scope: SemanticMemoryScope,
        kind: SemanticMemoryKind,
        content: str,
        content_hash: str,
        embedding: tuple[float, ...],
        embedding_provider: str,
        embedding_model: str,
        metadata: dict[str, Any],
    ) -> SemanticMemory:
        key = (scope, kind, content_hash)
        previous = self.records.get(key)
        now = datetime.now(UTC)
        memory = SemanticMemory(
            id=previous.id if previous else memory_id,
            scope=scope,
            kind=kind,
            content=content,
            content_hash=content_hash,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=len(embedding),
            metadata=metadata,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self.records[key] = memory
        return memory

    async def search(
        self,
        *,
        scope: SemanticMemoryScope,
        query_embedding: tuple[float, ...],
        kinds: tuple[SemanticMemoryKind, ...],
        limit: int,
        min_similarity: float,
    ) -> tuple[SemanticMemoryMatch, ...]:
        del query_embedding
        matches = [
            SemanticMemoryMatch(memory=memory, similarity=1.0)
            for (record_scope, kind, _), memory in self.records.items()
            if record_scope == scope and kind in kinds and 1.0 >= min_similarity
        ]
        return tuple(matches[:limit])


def _brand_scope(*, user_id: UUID, brand_id: UUID) -> SemanticMemoryScope:
    return SemanticMemoryScope(
        user_id=user_id,
        level=SemanticMemoryScopeLevel.BRAND,
        brand_id=brand_id,
    )


@pytest.mark.asyncio
async def test_stores_only_allowlisted_semantic_memory_kinds() -> None:
    repository = InMemorySemanticMemoryRepository()
    service = SemanticMemoryService(repository, MockEmbeddingProvider())
    scope = _brand_scope(user_id=uuid4(), brand_id=uuid4())

    for kind in SemanticMemoryKind:
        stored = await service.store(
            scope=scope,
            kind=kind,
            content=f"Reusable semantic lesson for {kind.value}",
            metadata={"source_id": str(uuid4()), "status": "approved"},
        )
        assert stored.kind is kind
        assert stored.embedding_dimension == 8

    assert {memory.kind for memory in repository.records.values()} == set(SemanticMemoryKind)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "3c32e4d6-a0c8-49ad-82f5-81fe5b9ed55f",
        "completed",
        "2026-08-26T12:30:00Z",
        "   ",
    ],
)
async def test_rejects_non_semantic_vector_content(content: str) -> None:
    service = SemanticMemoryService(
        InMemorySemanticMemoryRepository(),
        MockEmbeddingProvider(),
    )

    with pytest.raises(ValueError):
        await service.store(
            scope=_brand_scope(user_id=uuid4(), brand_id=uuid4()),
            kind=SemanticMemoryKind.BRAND_KNOWLEDGE,
            content=content,
        )


def test_scope_requires_one_exact_selector_and_brand_neutral_shared_scopes() -> None:
    user_id = uuid4()
    with pytest.raises(ValueError):
        SemanticMemoryScope(user_id=user_id, level=SemanticMemoryScopeLevel.BRAND)
    with pytest.raises(ValueError):
        SemanticMemoryScope(
            user_id=user_id,
            level=SemanticMemoryScopeLevel.PROJECT,
            project_id=uuid4(),
            brand_id=uuid4(),
        )
    with pytest.raises(ValueError):
        SemanticMemoryScope(
            user_id=user_id,
            level=SemanticMemoryScopeLevel.CATEGORY,
            category="hospitality",
        )

    category = SemanticMemoryScope(
        user_id=user_id,
        level=SemanticMemoryScopeLevel.CATEGORY,
        category=" Hospitality ",
        brand_neutral=True,
    )
    assert category.category == "hospitality"


@pytest.mark.asyncio
async def test_retrieval_never_crosses_user_or_brand_partition() -> None:
    repository = InMemorySemanticMemoryRepository()
    service = SemanticMemoryService(repository, MockEmbeddingProvider())
    user_a = uuid4()
    brand_a = _brand_scope(user_id=user_a, brand_id=uuid4())
    brand_b = _brand_scope(user_id=user_a, brand_id=uuid4())
    other_user_brand = _brand_scope(user_id=uuid4(), brand_id=brand_a.brand_id)  # type: ignore[arg-type]

    await service.store(
        scope=brand_a,
        kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
        content="Warm product close-ups outperform abstract scenes for this brand",
    )
    await service.store(
        scope=brand_b,
        kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
        content="Brand B private concept",
    )
    await service.store(
        scope=other_user_brand,
        kind=SemanticMemoryKind.SUCCESSFUL_CONCEPT,
        content="Another user's private concept",
    )

    matches = await service.retrieve(scope=brand_a, query="Which concepts performed well?")

    assert [match.memory.content for match in matches] == [
        "Warm product close-ups outperform abstract scenes for this brand"
    ]


@pytest.mark.asyncio
async def test_store_is_idempotent_inside_a_partition() -> None:
    repository = InMemorySemanticMemoryRepository()
    service = SemanticMemoryService(repository, MockEmbeddingProvider())
    scope = _brand_scope(user_id=uuid4(), brand_id=uuid4())

    first = await service.store(
        scope=scope,
        kind=SemanticMemoryKind.REJECTED_PATTERN,
        content="Avoid low-contrast calls to action on lifestyle photography",
    )
    second = await service.store(
        scope=scope,
        kind=SemanticMemoryKind.REJECTED_PATTERN,
        content="Avoid low-contrast calls to action on lifestyle photography",
    )

    assert first.id == second.id
    assert len(repository.records) == 1


@pytest.mark.asyncio
async def test_embedding_dimension_must_match_pgvector_schema() -> None:
    service = SemanticMemoryService(
        InMemorySemanticMemoryRepository(),
        MockEmbeddingProvider(dimension=7),
    )

    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        await service.retrieve(
            scope=_brand_scope(user_id=uuid4(), brand_id=uuid4()),
            query="brand voice",
        )


@pytest.mark.asyncio
async def test_empty_kind_filter_returns_no_memories() -> None:
    repository = InMemorySemanticMemoryRepository()
    service = SemanticMemoryService(repository, MockEmbeddingProvider())
    scope = _brand_scope(user_id=uuid4(), brand_id=uuid4())
    await service.store(
        scope=scope,
        kind=SemanticMemoryKind.BRAND_KNOWLEDGE,
        content="The brand voice is direct and optimistic",
    )

    assert await service.retrieve(scope=scope, query="voice", kinds=[]) == ()
