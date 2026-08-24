from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.posts import SQLAlchemyPostRepository
from app.infrastructure.database.session import get_db_transaction
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.services import PostsService


def get_post_scope(
    user_id: Annotated[UUID, Header(alias="X-User-ID")],
    project_id: Annotated[UUID, Header(alias="X-Project-ID")],
) -> PostScope:
    return PostScope(user_id=user_id, project_id=project_id)


def get_posts_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
) -> PostsService:
    return PostsService(SQLAlchemyPostRepository(session))


PostScopeDependency = Annotated[PostScope, Depends(get_post_scope)]
PostsServiceDependency = Annotated[PostsService, Depends(get_posts_service)]
