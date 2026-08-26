from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.domain.memory import (
    SemanticMemoryKind,
    SemanticMemoryScope,
)
from app.modules.posts.services.semantic_memory import SemanticMemoryService


class PostMemoryScopeResolver(Protocol):
    async def resolve_project_scope(self, *, post_id: UUID) -> SemanticMemoryScope | None: ...


class ConceptMemoryService:
    """Creative-selection memory policy over the generic semantic store."""

    def __init__(self, semantic_memory: SemanticMemoryService) -> None:
        self._semantic_memory = semantic_memory

    async def recall_rejected(
        self,
        *,
        scope: SemanticMemoryScope,
        query: str,
        limit: int = 10,
    ) -> Sequence[str]:
        matches = await self._semantic_memory.retrieve(
            scope=scope,
            query=query,
            kinds=(SemanticMemoryKind.REJECTED_CONCEPT,),
            limit=limit,
            min_similarity=0.2,
        )
        return tuple(match.memory.content for match in matches)

    async def remember_rejected(
        self,
        *,
        scope: SemanticMemoryScope,
        direction: CreativeDirection,
        generation_id: UUID,
    ) -> None:
        candidates = {item.id: item for item in direction.big_idea_candidates}
        for rejected in direction.rejected_concepts:
            candidate = candidates[rejected.candidate_id]
            content = (
                f"Rejected creative concept '{candidate.name}'. "
                f"Concept: {candidate.idea} "
                f"Reason: {rejected.rejection_reason}"
            )
            await self._semantic_memory.store(
                scope=scope,
                kind=SemanticMemoryKind.REJECTED_CONCEPT,
                content=content,
                metadata={
                    "generation_id": str(generation_id),
                    "candidate_id": candidate.id,
                    "rank": rejected.rank,
                    "total_score": rejected.total_score,
                    "selection_scores": candidate.evaluation.selection_scores(),
                    "weakness": rejected.weakness,
                    "winning_candidate_id": direction.winning_concept.candidate_id,
                },
            )


__all__ = ["ConceptMemoryService", "PostMemoryScopeResolver"]
