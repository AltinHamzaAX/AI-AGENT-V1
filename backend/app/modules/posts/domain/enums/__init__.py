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


class GenerationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


class GenerationArtifactKind(StrEnum):
    INTERMEDIATE = "intermediate"
    PREVIEW = "preview"
    FINAL = "final"


class PostWorkflowSection(StrEnum):
    SUPERVISOR = "supervisor"
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
    SCENE_PURITY = "scene_purity"
    POST_DRAFT = "post_draft"
    VERIFICATION = "verification"
    QUALITY = "quality"
    DESIGN_QUALITY = "design_quality"
    QUALITY_APPROVAL = "quality_approval"
    REVISION_HISTORY = "revision_history"


class UnderstandingField(StrEnum):
    BUSINESS = "business"
    BRAND = "brand"
    PRODUCT_SERVICE = "product_service"
    GOAL = "goal"
    AUDIENCE = "audience"
    MARKET = "market"
    LOCATION = "location"
    PLATFORM = "platform"
    LANGUAGE = "language"
    OFFER = "offer"
    CTA_INTENT = "cta_intent"


__all__ = [
    "GenerationArtifactKind",
    "GenerationJobStatus",
    "GenerationStatus",
    "PostWorkflowSection",
    "UnderstandingField",
]
