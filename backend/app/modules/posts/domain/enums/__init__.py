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


__all__ = ["GenerationArtifactKind", "GenerationStatus"]
