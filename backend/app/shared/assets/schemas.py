from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.shared.assets.domain import Asset, AssetRole, AssetUploadResult


class AssetRead(BaseModel):
    id: UUID
    message_id: UUID
    role: AssetRole
    original_filename: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    checksum: str
    metadata: dict[str, JsonValue]
    created_at: datetime

    @classmethod
    def from_domain(cls, asset: Asset) -> Self:
        return cls(
            id=asset.id,
            message_id=asset.message_id,
            role=asset.role,
            original_filename=asset.original_filename,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            size_bytes=asset.size_bytes,
            checksum=asset.checksum,
            metadata=asset.metadata,
            created_at=asset.created_at,
        )


class AssetUploadRead(AssetRead):
    deduplicated: bool

    @classmethod
    def from_result(cls, result: AssetUploadResult) -> Self:
        return cls(
            **AssetRead.from_domain(result.asset).model_dump(), deduplicated=result.deduplicated
        )
