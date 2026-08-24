from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.dependencies.assets import AssetServiceDependency
from app.dependencies.conversations import ConversationScopeDependency
from app.shared.assets.domain import (
    AssetMessageNotFoundError,
    AssetNotFoundError,
    AssetRole,
    AssetStorageError,
    AssetValidationError,
)
from app.shared.assets.schemas import AssetRead, AssetUploadRead

router = APIRouter()

READ_CHUNK_SIZE = 1024 * 1024


@router.post("", response_model=AssetUploadRead, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    message_id: Annotated[UUID, Form()],
    role: Annotated[AssetRole, Form()],
    file: Annotated[UploadFile, File()],
    scope: ConversationScopeDependency,
    service: AssetServiceDependency,
) -> AssetUploadRead:
    try:
        data = await _read_upload(file, limit=service.max_size_bytes)
        result = await service.upload(
            scope=scope,
            message_id=message_id,
            role=role,
            original_filename=file.filename or "",
            declared_mime_type=file.content_type,
            data=data,
        )
    except AssetMessageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Message not found") from exc
    except AssetValidationError as exc:
        response_status = 413 if exc.code == "file_too_large" else 422
        raise HTTPException(
            status_code=response_status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except AssetStorageError as exc:
        raise HTTPException(status_code=503, detail="Object storage is unavailable") from exc
    finally:
        await file.close()
    return AssetUploadRead.from_result(result)


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: UUID,
    scope: ConversationScopeDependency,
    service: AssetServiceDependency,
) -> AssetRead:
    try:
        asset = await service.get(asset_id=asset_id, scope=scope)
    except AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc
    return AssetRead.from_domain(asset)


@router.get("", response_model=list[AssetRead])
async def list_message_assets(
    message_id: UUID,
    scope: ConversationScopeDependency,
    service: AssetServiceDependency,
) -> list[AssetRead]:
    try:
        assets = await service.list_for_message(message_id=message_id, scope=scope)
    except AssetMessageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Message not found") from exc
    return [AssetRead.from_domain(asset) for asset in assets]


async def _read_upload(file: UploadFile, *, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(READ_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            raise AssetValidationError(
                f"The uploaded file exceeds the {limit}-byte limit",
                code="file_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)
