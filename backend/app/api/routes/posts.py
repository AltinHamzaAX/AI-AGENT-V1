from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.dependencies.posts import PostScopeDependency, PostsServiceDependency
from app.modules.posts.domain.exceptions import (
    PostGenerationNotFoundError,
    PostNotFoundError,
    PostSourceNotFoundError,
)
from app.modules.posts.schemas import (
    GenerationArtifactRead,
    PostCreate,
    PostGenerationRead,
    PostRead,
)

router = APIRouter()


@router.post("", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: PostCreate,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> PostRead:
    try:
        post = await service.create_post(
            scope=scope,
            conversation_id=payload.conversation_id,
            campaign_id=payload.campaign_id,
            title=payload.title,
        )
    except PostSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return PostRead.from_domain(post)


@router.get("/{post_id}", response_model=PostRead)
async def get_post(
    post_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> PostRead:
    try:
        post = await service.get_post(post_id=post_id, scope=scope)
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post not found") from exc
    return PostRead.from_domain(post)


@router.post(
    "/{post_id}/generations",
    response_model=PostGenerationRead,
    status_code=status.HTTP_201_CREATED,
)
async def request_generation(
    post_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> PostGenerationRead:
    try:
        generation = await service.request_generation(post_id=post_id, scope=scope)
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post not found") from exc
    return PostGenerationRead.from_domain(generation)


@router.get("/{post_id}/generations", response_model=list[PostGenerationRead])
async def list_generations(
    post_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> list[PostGenerationRead]:
    try:
        generations = await service.list_generations(post_id=post_id, scope=scope)
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post not found") from exc
    return [PostGenerationRead.from_domain(generation) for generation in generations]


@router.get(
    "/{post_id}/generations/{generation_id}/artifacts",
    response_model=list[GenerationArtifactRead],
)
async def list_generation_artifacts(
    post_id: UUID,
    generation_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> list[GenerationArtifactRead]:
    try:
        artifacts = await service.list_artifacts(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    return [GenerationArtifactRead.from_domain(artifact) for artifact in artifacts]
