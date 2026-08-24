from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status

from app.dependencies.posts import PostScopeDependency, PostsServiceDependency
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.exceptions import (
    PostGenerationNotFoundError,
    PostNotFoundError,
    PostSourceNotFoundError,
    SemanticContractHardFailError,
    SemanticContractNotFoundError,
    WorkflowStateConflictError,
)
from app.modules.posts.schemas import (
    GenerationArtifactRead,
    GenerationJobRead,
    PostCreate,
    PostGenerationRead,
    PostGenerationStateRead,
    PostGenerationStateVersionRead,
    PostRead,
    SemanticAssertionsRequest,
    SemanticContractCreate,
    SemanticContractStateRead,
    SemanticValidationRead,
    WorkflowSectionWrite,
)

router = APIRouter()


def _semantic_hard_fail(exc: SemanticContractHardFailError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "SEMANTIC_CONTRACT_HARD_FAIL",
            "decision": "HARD_FAIL",
            "violations": list(exc.violations),
        },
    )


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
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PostGenerationRead:
    try:
        generation = await service.request_generation(
            post_id=post_id,
            scope=scope,
            idempotency_key=idempotency_key,
        )
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PostGenerationRead.from_domain(generation)


@router.get(
    "/{post_id}/generations/{generation_id}/job",
    response_model=GenerationJobRead,
)
async def get_generation_job(
    post_id: UUID,
    generation_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> GenerationJobRead:
    try:
        job = await service.get_generation_job(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    return GenerationJobRead.from_domain(job)


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


@router.get(
    "/{post_id}/generations/{generation_id}/state",
    response_model=PostGenerationStateRead,
)
async def get_generation_state(
    post_id: UUID,
    generation_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> PostGenerationStateRead:
    try:
        workflow_state = await service.get_workflow_state(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    return PostGenerationStateRead.from_domain(workflow_state)


@router.get(
    "/{post_id}/generations/{generation_id}/state/versions",
    response_model=list[PostGenerationStateVersionRead],
)
async def list_generation_state_versions(
    post_id: UUID,
    generation_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> list[PostGenerationStateVersionRead]:
    try:
        snapshots = await service.list_workflow_state_versions(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    return [PostGenerationStateVersionRead.from_domain(item) for item in snapshots]


@router.patch(
    "/{post_id}/generations/{generation_id}/state/{section}",
    response_model=PostGenerationStateRead,
)
async def write_generation_state_section(
    post_id: UUID,
    generation_id: UUID,
    section: PostWorkflowSection,
    payload: WorkflowSectionWrite,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> PostGenerationStateRead:
    try:
        workflow_state = await service.write_workflow_section(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            section=section,
            value=payload.value,
            expected_version=payload.expected_version,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    except WorkflowStateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow state version conflict; read the latest state and retry",
        ) from exc
    except SemanticContractHardFailError as exc:
        raise _semantic_hard_fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PostGenerationStateRead.from_domain(workflow_state)


@router.put(
    "/{post_id}/generations/{generation_id}/semantic-contract",
    response_model=SemanticContractStateRead,
)
async def create_semantic_contract(
    post_id: UUID,
    generation_id: UUID,
    payload: SemanticContractCreate,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> SemanticContractStateRead:
    try:
        contract, workflow_state = await service.create_semantic_contract(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            contract=payload.to_domain(),
            expected_version=payload.expected_version,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    except WorkflowStateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow state version conflict; read the latest state and retry",
        ) from exc
    except SemanticContractHardFailError as exc:
        raise _semantic_hard_fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SemanticContractStateRead.from_domain(contract, workflow_state)


@router.get(
    "/{post_id}/generations/{generation_id}/semantic-contract",
    response_model=SemanticContractStateRead,
)
async def get_semantic_contract(
    post_id: UUID,
    generation_id: UUID,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> SemanticContractStateRead:
    try:
        contract, workflow_state = await service.get_semantic_contract(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    except SemanticContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Semantic contract not found") from exc
    except SemanticContractHardFailError as exc:
        raise _semantic_hard_fail(exc) from exc
    return SemanticContractStateRead.from_domain(contract, workflow_state)


@router.post(
    "/{post_id}/generations/{generation_id}/semantic-contract/validate",
    response_model=SemanticValidationRead,
)
async def validate_semantic_contract(
    post_id: UUID,
    generation_id: UUID,
    payload: SemanticAssertionsRequest,
    scope: PostScopeDependency,
    service: PostsServiceDependency,
) -> SemanticValidationRead:
    try:
        contract = await service.validate_semantic_assertions(
            generation_id=generation_id,
            post_id=post_id,
            scope=scope,
            assertions=payload.to_domain(),
        )
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    except SemanticContractNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Semantic contract not found") from exc
    except SemanticContractHardFailError as exc:
        raise _semantic_hard_fail(exc) from exc
    return SemanticValidationRead(fingerprint=contract.fingerprint)
