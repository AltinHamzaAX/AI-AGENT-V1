from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.dependencies.providers import get_provider_bundle
from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.repositories.semantic_memory import (
    SQLAlchemySemanticMemoryRepository,
)
from app.infrastructure.database.session import get_db_transaction
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.providers import ProviderBundle
from app.modules.posts.services import PostsService, SemanticMemoryService


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


def get_semantic_memory_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
    providers: Annotated[ProviderBundle, Depends(get_provider_bundle)],
) -> SemanticMemoryService:
    return SemanticMemoryService(
        SQLAlchemySemanticMemoryRepository(session),
        providers.embedding,
    )


PostScopeDependency = Annotated[PostScope, Depends(get_post_scope)]
PostsServiceDependency = Annotated[PostsService, Depends(get_posts_service)]
SemanticMemoryServiceDependency = Annotated[
    SemanticMemoryService,
    Depends(get_semantic_memory_service),
]
