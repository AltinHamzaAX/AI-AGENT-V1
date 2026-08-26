from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posts import PostSemanticMemoryModel
from app.modules.posts.domain.memory import (
    SemanticMemory,
    SemanticMemoryKind,
    SemanticMemoryMatch,
    SemanticMemoryScope,
)

EMBEDDING_DIMENSION = 768


def _scope_key(scope: SemanticMemoryScope) -> str:
    if scope.brand_id is not None:
        return str(scope.brand_id)
    if scope.project_id is not None:
        return str(scope.project_id)
    return scope.category or "global"


def _memory(model: PostSemanticMemoryModel) -> SemanticMemory:
    return SemanticMemory(
        id=model.id,
        scope=SemanticMemoryScope(
            user_id=model.user_id,
            level=model.scope_level,
            brand_id=model.brand_id,
            project_id=model.project_id,
            category=model.category,
            brand_neutral=model.brand_neutral,
        ),
        kind=SemanticMemoryKind(model.kind),
        content=model.content,
        content_hash=model.content_hash,
        embedding_provider=model.embedding_provider,
        embedding_model=model.embedding_model,
        embedding_dimension=model.embedding_dimension,
        metadata=dict(model.memory_metadata),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SQLAlchemySemanticMemoryRepository:
    embedding_dimension = EMBEDDING_DIMENSION

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        metadata: dict[str, object],
    ) -> SemanticMemory:
        values = {
            "id": memory_id,
            "user_id": scope.user_id,
            "scope_level": scope.level.value,
            "scope_key": _scope_key(scope),
            "brand_id": scope.brand_id,
            "project_id": scope.project_id,
            "category": scope.category,
            "brand_neutral": scope.brand_neutral,
            "kind": kind.value,
            "content": content,
            "content_hash": content_hash,
            "embedding": list(embedding),
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_dimension": len(embedding),
            "memory_metadata": metadata,
        }
        statement = insert(PostSemanticMemoryModel).values(**values)
        statement = statement.on_conflict_do_update(
            constraint="uq_post_semantic_memories_partition_content",
            set_={
                PostSemanticMemoryModel.content: statement.excluded.content,
                PostSemanticMemoryModel.embedding: statement.excluded.embedding,
                PostSemanticMemoryModel.embedding_provider: (
                    statement.excluded.embedding_provider
                ),
                PostSemanticMemoryModel.embedding_model: statement.excluded.embedding_model,
                PostSemanticMemoryModel.embedding_dimension: (
                    statement.excluded.embedding_dimension
                ),
                PostSemanticMemoryModel.memory_metadata: statement.excluded.metadata,
                PostSemanticMemoryModel.updated_at: func.now(),
            },
        ).returning(PostSemanticMemoryModel)
        model = (await self._session.execute(statement)).scalar_one()
        return _memory(model)

    async def search(
        self,
        *,
        scope: SemanticMemoryScope,
        query_embedding: tuple[float, ...],
        kinds: tuple[SemanticMemoryKind, ...],
        limit: int,
        min_similarity: float,
    ) -> Sequence[SemanticMemoryMatch]:
        distance = PostSemanticMemoryModel.embedding.cosine_distance(list(query_embedding))
        statement = (
            select(PostSemanticMemoryModel, distance.label("distance"))
            .where(
                PostSemanticMemoryModel.user_id == scope.user_id,
                PostSemanticMemoryModel.scope_level == scope.level.value,
                PostSemanticMemoryModel.scope_key == _scope_key(scope),
                PostSemanticMemoryModel.brand_neutral == scope.brand_neutral,
                PostSemanticMemoryModel.kind.in_(kind.value for kind in kinds),
                distance <= 1.0 - min_similarity,
            )
            .order_by(
                distance,
                PostSemanticMemoryModel.created_at.desc(),
                PostSemanticMemoryModel.id,
            )
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(
            SemanticMemoryMatch(
                memory=_memory(model),
                similarity=max(0.0, min(1.0, 1.0 - float(row_distance))),
            )
            for model, row_distance in rows
        )


__all__ = ["EMBEDDING_DIMENSION", "SQLAlchemySemanticMemoryRepository"]
