"""SQLAlchemy repository adapters."""

from app.infrastructure.database.repositories.assets import SQLAlchemyAssetRepository
from app.infrastructure.database.repositories.base import SQLAlchemyRepository
from app.infrastructure.database.repositories.campaigns import SQLAlchemyCampaignRepository
from app.infrastructure.database.repositories.conversations import (
    SQLAlchemyConversationRepository,
)
from app.infrastructure.database.repositories.execution_traces import (
    SQLAlchemyExecutionTraceRecorder,
)
from app.infrastructure.database.repositories.generation_jobs import (
    SQLAlchemyGenerationJobRepository,
)
from app.infrastructure.database.repositories.post_memory_scope import (
    SQLAlchemyPostMemoryScopeResolver,
)
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.repositories.semantic_memory import (
    SQLAlchemySemanticMemoryRepository,
)
from app.infrastructure.database.repositories.supervisor import (
    SQLAlchemySupervisorCheckpointStore,
)
from app.infrastructure.database.repositories.worker_semantic_memory import (
    WorkerPostMemoryScopeResolver,
    WorkerSemanticMemoryRepository,
)

__all__ = [
    "SQLAlchemyAssetRepository",
    "SQLAlchemyCampaignRepository",
    "SQLAlchemyConversationRepository",
    "SQLAlchemyPostRepository",
    "SQLAlchemyPostMemoryScopeResolver",
    "SQLAlchemyGenerationJobRepository",
    "SQLAlchemyExecutionTraceRecorder",
    "SQLAlchemyRepository",
    "SQLAlchemySemanticMemoryRepository",
    "WorkerPostMemoryScopeResolver",
    "WorkerSemanticMemoryRepository",
    "SQLAlchemySupervisorCheckpointStore",
]
