from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.posts.agents.art_director import ArtDirection
from app.modules.posts.agents.copywriter import CopyDraft
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.memory import (
    SemanticMemoryKind,
    SemanticMemoryScope,
)
from app.modules.posts.services.semantic_memory import SemanticMemoryService
from app.modules.posts.tools.creative import CreativeDNA, extract_creative_dna


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

    async def recall_approved(
        self,
        *,
        scope: SemanticMemoryScope,
        query: str,
        limit: int = 12,
    ) -> tuple[CreativeDNA, ...]:
        matches = await self._semantic_memory.retrieve(
            scope=scope,
            query=query,
            kinds=(SemanticMemoryKind.APPROVED_CREATIVE,),
            limit=limit,
            min_similarity=0.1,
        )
        recalled: list[CreativeDNA] = []
        for match in matches:
            metadata = getattr(match.memory, "metadata", {})
            raw = metadata.get("creative_dna") if isinstance(metadata, dict) else None
            if not isinstance(raw, dict):
                continue
            try:
                dna = CreativeDNA.model_validate(raw)
            except ValueError:
                continue
            if dna.fingerprint not in {item.fingerprint for item in recalled}:
                recalled.append(dna)
        return tuple(recalled)

    async def remember_approved(
        self,
        *,
        scope: SemanticMemoryScope,
        direction: CreativeDirection,
        copy: CopyDraft,
        art: ArtDirection,
        design_spec: DesignSpec,
        generation_id: UUID,
    ) -> CreativeDNA:
        dna = extract_creative_dna(
            direction=direction,
            copy=copy,
            art=art,
            spec=design_spec,
        )
        await self._semantic_memory.store(
            scope=scope,
            kind=SemanticMemoryKind.APPROVED_CREATIVE,
            content=(
                f"Approved creative DNA. Concept: {dna.concept} Visual hook: {dna.visual_hook} "
                f"Composition: {dna.composition} Graphic system: {dna.graphic_system}"
            ),
            metadata={
                "generation_id": str(generation_id),
                "creative_dna": dna.model_dump(mode="json"),
                "creative_dna_fingerprint": dna.fingerprint,
            },
        )
        return dna


__all__ = ["ConceptMemoryService", "PostMemoryScopeResolver"]
