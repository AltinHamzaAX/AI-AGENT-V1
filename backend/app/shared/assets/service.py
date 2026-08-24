from contextlib import suppress
from uuid import UUID, uuid4

from app.shared.assets.contracts import AssetRepository, AssetStorage
from app.shared.assets.domain import (
    Asset,
    AssetMessageNotFoundError,
    AssetNotFoundError,
    AssetRole,
    AssetStorageError,
    AssetUploadResult,
)
from app.shared.assets.validation import validate_image_upload
from app.shared.conversations.domain import ConversationScope


class AssetService:
    def __init__(
        self,
        *,
        repository: AssetRepository,
        storage: AssetStorage,
        max_size_bytes: int,
        max_dimension: int,
        max_pixels: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self.max_size_bytes = max_size_bytes
        self._max_dimension = max_dimension
        self._max_pixels = max_pixels

    async def upload(
        self,
        *,
        scope: ConversationScope,
        message_id: UUID,
        role: AssetRole,
        original_filename: str,
        declared_mime_type: str | None,
        data: bytes,
    ) -> AssetUploadResult:
        validated = validate_image_upload(
            data=data,
            original_filename=original_filename,
            declared_mime_type=declared_mime_type,
            max_size_bytes=self.max_size_bytes,
            max_dimension=self._max_dimension,
            max_pixels=self._max_pixels,
        )
        if not await self._repository.message_exists(message_id=message_id, scope=scope):
            raise AssetMessageNotFoundError

        existing = await self._repository.find_by_checksum(
            scope=scope,
            checksum=validated.checksum,
        )
        if existing is not None and existing.message_id == message_id:
            return AssetUploadResult(asset=existing, deduplicated=True)

        asset_id = uuid4()
        storage_key = (
            existing.storage_key
            if existing is not None
            else f"assets/{scope.project_id}/{asset_id}{validated.extension}"
        )
        object_created = existing is None
        if object_created:
            try:
                await self._storage.put(
                    key=storage_key,
                    data=data,
                    content_type=validated.mime_type,
                    metadata={"checksum": validated.checksum},
                )
            except Exception as exc:
                raise AssetStorageError("Object storage upload failed") from exc

        try:
            asset = await self._repository.create(
                asset_id=asset_id,
                scope=scope,
                message_id=message_id,
                role=role,
                original_filename=validated.original_filename,
                mime_type=validated.mime_type,
                width=validated.width,
                height=validated.height,
                size_bytes=validated.size_bytes,
                storage_key=storage_key,
                checksum=validated.checksum,
                metadata=validated.metadata,
            )
        except BaseException:
            if object_created:
                with suppress(Exception):
                    await self._storage.delete(key=storage_key)
            raise
        return AssetUploadResult(asset=asset, deduplicated=existing is not None)

    async def get(self, *, asset_id: UUID, scope: ConversationScope) -> Asset:
        asset = await self._repository.get(asset_id=asset_id, scope=scope)
        if asset is None:
            raise AssetNotFoundError
        return asset

    async def list_for_message(
        self,
        *,
        message_id: UUID,
        scope: ConversationScope,
    ) -> tuple[Asset, ...]:
        assets = await self._repository.list_for_message(message_id=message_id, scope=scope)
        if assets is None:
            raise AssetMessageNotFoundError
        return tuple(assets)
