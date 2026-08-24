from enum import StrEnum


class GenerationStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    REVIEWING = "reviewing"
    REVISION = "revision"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationArtifactKind(StrEnum):
    INTERMEDIATE = "intermediate"
    PREVIEW = "preview"
    FINAL = "final"


class PostWorkflowSection(StrEnum):
    CONVERSATION_CONTEXT = "conversation_context"
    BRIEF = "brief"
    SEMANTIC_CONTRACT = "semantic_contract"
    BRAND = "brand"
    PRODUCT = "product"
    ASSETS = "assets"
    AUDIENCE = "audience"
    RESEARCH = "research"
    MARKETING_STRATEGY = "marketing_strategy"
    CREATIVE_CONCEPT = "creative_concept"
    COPY = "copy"
    ART_DIRECTION = "art_direction"
    DESIGN_SPEC = "design_spec"
    GENERATION_PLAN = "generation_plan"
    GENERATION_ARTIFACTS = "generation_artifacts"
    QUALITY = "quality"
    REVISION_HISTORY = "revision_history"


__all__ = ["GenerationArtifactKind", "GenerationStatus", "PostWorkflowSection"]
