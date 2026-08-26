import hashlib
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from app.modules.posts.domain.memory import (
    SemanticMemory,
    SemanticMemoryKind,
    SemanticMemoryMatch,
    SemanticMemoryScope,
)
from app.modules.posts.providers import EmbeddingProvider, EmbeddingRequest
from app.modules.posts.repositories import SemanticMemoryRepository

_UUID_ONLY = re.compile(
    r"^[{(]?[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}[)}]?$",
    re.IGNORECASE,
)
_TIMESTAMP_ONLY = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_STATUS_ONLY = frozenset(
    {
        "pending",
        "queued",
        "running",
        "reviewing",
        "revision",
        "completed",
        "failed",
        "cancelled",
        "approved",
        "rejected",
    }
)


class SemanticMemoryService:
    """Embeds only semantic content and delegates exact-scope retrieval."""

    def __init__(
        self,
        repository: SemanticMemoryRepository,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider

    async def store(
        self,
        *,
        scope: SemanticMemoryScope,
        kind: SemanticMemoryKind,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> SemanticMemory:
        kind = SemanticMemoryKind(kind)
        normalized = _normalize_semantic_text(content)
        embedding, provider, model = await self._embed(normalized)
        return await self._repository.upsert(
            memory_id=uuid4(),
            scope=scope,
            kind=kind,
            content=normalized,
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            embedding=embedding,
            embedding_provider=provider,
            embedding_model=model,
            metadata=dict(metadata or {}),
        )

    async def retrieve(
        self,
        *,
        scope: SemanticMemoryScope,
        query: str,
        kinds: Sequence[SemanticMemoryKind] | None = None,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> Sequence[SemanticMemoryMatch]:
        if not 1 <= limit <= 100:
            raise ValueError("Semantic memory retrieval limit must be between 1 and 100")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("Semantic memory min_similarity must be between 0 and 1")
        requested_kinds = SemanticMemoryKind if kinds is None else kinds
        selected_kinds = tuple(SemanticMemoryKind(kind) for kind in requested_kinds)
        if not selected_kinds:
            return ()
        normalized = _normalize_semantic_text(query)
        embedding, _, _ = await self._embed(normalized)
        return await self._repository.search(
            scope=scope,
            query_embedding=embedding,
            kinds=selected_kinds,
            limit=limit,
            min_similarity=min_similarity,
        )

    async def _embed(self, text: str) -> tuple[tuple[float, ...], str, str]:
        response = await self._embedding_provider.embed(EmbeddingRequest(texts=(text,)))
        if len(response.vectors) != 1:
            raise ValueError("Embedding provider must return exactly one vector")
        embedding = response.vectors[0]
        expected = self._repository.embedding_dimension
        if len(embedding) != expected:
            raise ValueError(
                f"Embedding dimension mismatch: expected {expected}, received {len(embedding)}"
            )
        return embedding, response.provider, response.model


def _normalize_semantic_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Semantic memory content cannot be empty")
    folded = normalized.casefold()
    if _UUID_ONLY.fullmatch(normalized) or _TIMESTAMP_ONLY.fullmatch(normalized):
        raise ValueError("IDs and timestamps must be stored as relational metadata, not vectors")
    if folded in _STATUS_ONLY:
        raise ValueError("Statuses must be stored as relational fields, not vectors")
    return normalized


__all__ = ["SemanticMemoryService"]
