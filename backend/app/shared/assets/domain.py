from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.shared.conversations.domain import ConversationScope


class AssetRole(StrEnum):
    LOGO = "logo"
    PRODUCT = "product"
    VEHICLE = "vehicle"
    PACKAGING = "packaging"
    ENVIRONMENT = "environment"
    BACKGROUND = "background"
    PERSON = "person"
    STYLE_REFERENCE = "style_reference"
    INSPIRATION = "inspiration"
    SUPPORTING_ASSET = "supporting_asset"


@dataclass(frozen=True, slots=True)
class Asset:
    id: UUID
    scope: ConversationScope
    message_id: UUID
    role: AssetRole
    original_filename: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    storage_key: str
    checksum: str
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidatedAssetUpload:
    original_filename: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    checksum: str
    extension: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssetUploadResult:
    asset: Asset
    deduplicated: bool


class AssetNotFoundError(LookupError):
    pass


class AssetMessageNotFoundError(LookupError):
    pass


class AssetValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_asset") -> None:
        super().__init__(message)
        self.code = code


class AssetStorageError(RuntimeError):
    pass
