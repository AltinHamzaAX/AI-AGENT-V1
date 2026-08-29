from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.dependencies.conversations import ConversationServiceDependency
from app.dependencies.providers import get_provider_bundle
from app.infrastructure.database.repositories.assets import SQLAlchemyAssetRepository
from app.infrastructure.database.repositories.post_benchmarks import (
    SQLAlchemyBenchmarkReviewRepository,
)
from app.infrastructure.database.repositories.post_conversation_contexts import (
    SQLAlchemyPostConversationContextRepository,
)
from app.infrastructure.database.repositories.post_memory_scope import (
    SQLAlchemyPostMemoryScopeResolver,
)
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.repositories.semantic_memory import (
    SQLAlchemySemanticMemoryRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.modules.posts.chat import ConversationResponder, ConversationRouter
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.services import (
    BenchmarkService,
    ConceptMemoryService,
    PostsService,
    SemanticMemoryService,
)
from app.modules.posts.services.chat import PostChatService


def get_post_scope(
    user_id: Annotated[UUID, Header(alias="X-User-ID")],
    project_id: Annotated[UUID, Header(alias="X-Project-ID")],
) -> PostScope:
    return PostScope(user_id=user_id, project_id=project_id)


def get_posts_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> PostsService:
    settings = get_settings()
    return PostsService(
        SQLAlchemyPostRepository(session),
        generation_job_max_attempts=settings.generation_job_max_attempts,
        generation_job_timeout_seconds=settings.generation_job_timeout_seconds,
    )


def get_benchmark_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
    posts: Annotated[PostsService, Depends(get_posts_service)],
) -> BenchmarkService:
    return BenchmarkService(SQLAlchemyBenchmarkReviewRepository(session), posts)


def get_post_chat_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
    conversations: ConversationServiceDependency,
    posts: Annotated[PostsService, Depends(get_posts_service)],
    providers: Annotated[ProviderBundle, Depends(get_provider_bundle)],
) -> PostChatService:
    return PostChatService(
        conversations=conversations,
        posts=posts,
        contexts=SQLAlchemyPostConversationContextRepository(session),
        assets=SQLAlchemyAssetRepository(session),
        # Classifying a message reads what is already there; writing the reply
        # has to be thought up, so it follows the creative model when one is
        # configured and shares the default model when it is not.
        router=ConversationRouter(providers.llm),
        responder=ConversationResponder(providers.creative_llm),
    )


def get_semantic_memory_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
    providers: Annotated[ProviderBundle, Depends(get_provider_bundle)],
) -> SemanticMemoryService:
    return SemanticMemoryService(
        SQLAlchemySemanticMemoryRepository(session),
        providers.embedding,
    )


def get_concept_memory_service(
    semantic_memory: Annotated[SemanticMemoryService, Depends(get_semantic_memory_service)],
) -> ConceptMemoryService:
    return ConceptMemoryService(semantic_memory)


def get_post_memory_scope_resolver(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> SQLAlchemyPostMemoryScopeResolver:
    return SQLAlchemyPostMemoryScopeResolver(session)


PostScopeDependency = Annotated[PostScope, Depends(get_post_scope)]
PostsServiceDependency = Annotated[PostsService, Depends(get_posts_service)]
BenchmarkServiceDependency = Annotated[BenchmarkService, Depends(get_benchmark_service)]
PostChatServiceDependency = Annotated[PostChatService, Depends(get_post_chat_service)]
SemanticMemoryServiceDependency = Annotated[
    SemanticMemoryService,
    Depends(get_semantic_memory_service),
]
ConceptMemoryServiceDependency = Annotated[
    ConceptMemoryService,
    Depends(get_concept_memory_service),
]
