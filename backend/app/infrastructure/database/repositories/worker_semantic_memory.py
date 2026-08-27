from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.posts.domain.memory import (
    SemanticMemory,
    SemanticMemoryKind,
    SemanticMemoryMatch,
    SemanticMemoryScope,
)

from .post_memory_scope import SQLAlchemyPostMemoryScopeResolver
from .semantic_memory import EMBEDDING_DIMENSION, SQLAlchemySemanticMemoryRepository


class WorkerSemanticMemoryRepository:
    """Transaction-per-operation semantic memory adapter for long-lived workers."""

    embedding_dimension = EMBEDDING_DIMENSION

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory.begin() as session:
            return await SQLAlchemySemanticMemoryRepository(session).upsert(
                memory_id=memory_id,
                scope=scope,
                kind=kind,
                content=content,
                content_hash=content_hash,
                embedding=embedding,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                metadata=metadata,
            )

    async def search(
        self,
        *,
        scope: SemanticMemoryScope,
        query_embedding: tuple[float, ...],
        kinds: tuple[SemanticMemoryKind, ...],
        limit: int,
        min_similarity: float,
    ) -> Sequence[SemanticMemoryMatch]:
        async with self._session_factory.begin() as session:
            return await SQLAlchemySemanticMemoryRepository(session).search(
                scope=scope,
                query_embedding=query_embedding,
                kinds=kinds,
                limit=limit,
                min_similarity=min_similarity,
            )


class WorkerPostMemoryScopeResolver:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve_project_scope(self, *, post_id: UUID) -> SemanticMemoryScope | None:
        async with self._session_factory.begin() as session:
            return await SQLAlchemyPostMemoryScopeResolver(session).resolve_project_scope(
                post_id=post_id
            )


__all__ = ["WorkerPostMemoryScopeResolver", "WorkerSemanticMemoryRepository"]
