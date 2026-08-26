from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class SemanticMemoryKind(StrEnum):
    BRAND_KNOWLEDGE = "brand_knowledge"
    APPROVED_CREATIVE = "approved_creative"
    RESEARCH_SUMMARY = "research_summary"
    SUCCESSFUL_CONCEPT = "successful_concept"
    VISUAL_REFERENCE = "visual_reference"
    DESIGNER_FEEDBACK = "designer_feedback"
    REJECTED_CONCEPT = "rejected_concept"
    REJECTED_PATTERN = "rejected_pattern"


class SemanticMemoryScopeLevel(StrEnum):
    BRAND = "brand"
    PROJECT = "project"
    CATEGORY = "category"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class SemanticMemoryScope:
    """An exact retrieval partition inside one user/tenant boundary."""

    user_id: UUID
    level: SemanticMemoryScopeLevel
    brand_id: UUID | None = None
    project_id: UUID | None = None
    category: str | None = None
    brand_neutral: bool = False

    def __post_init__(self) -> None:
        level = SemanticMemoryScopeLevel(self.level)
        object.__setattr__(self, "level", level)
        category = self.category.strip().casefold() if self.category else None
        object.__setattr__(self, "category", category)

        selectors = {
            SemanticMemoryScopeLevel.BRAND: (
                self.brand_id is not None and self.project_id is None and category is None
            ),
            SemanticMemoryScopeLevel.PROJECT: (
                self.project_id is not None and self.brand_id is None and category is None
            ),
            SemanticMemoryScopeLevel.CATEGORY: (
                category is not None and self.brand_id is None and self.project_id is None
            ),
            SemanticMemoryScopeLevel.GLOBAL: (
                self.brand_id is None and self.project_id is None and category is None
            ),
        }
        if not selectors[level]:
            raise ValueError(f"Invalid selectors for semantic memory scope '{level.value}'")
        if level in {
            SemanticMemoryScopeLevel.CATEGORY,
            SemanticMemoryScopeLevel.GLOBAL,
        } and not self.brand_neutral:
            raise ValueError(f"Semantic memory scope '{level.value}' must be brand-neutral")
        if level in {
            SemanticMemoryScopeLevel.BRAND,
            SemanticMemoryScopeLevel.PROJECT,
        } and self.brand_neutral:
            raise ValueError(f"Semantic memory scope '{level.value}' cannot be brand-neutral")


@dataclass(frozen=True, slots=True)
class SemanticMemory:
    id: UUID
    scope: SemanticMemoryScope
    kind: SemanticMemoryKind
    content: str
    content_hash: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SemanticMemoryMatch:
    memory: SemanticMemory
    similarity: float


__all__ = [
    "SemanticMemory",
    "SemanticMemoryKind",
    "SemanticMemoryMatch",
    "SemanticMemoryScope",
    "SemanticMemoryScopeLevel",
]
