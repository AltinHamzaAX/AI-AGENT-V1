from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from app.shared.assets.domain import Asset, AssetRole
from app.shared.conversations.domain import ConversationScope


class AssetRepository(Protocol):
    async def message_exists(
        self,
        *,
        message_id: UUID,
        scope: ConversationScope,
    ) -> bool: ...

    async def find_by_checksum(
        self,
        *,
        scope: ConversationScope,
        checksum: str,
    ) -> Asset | None: ...

    async def create(
        self,
        *,
        asset_id: UUID,
        scope: ConversationScope,
        message_id: UUID,
        role: AssetRole,
        original_filename: str,
        mime_type: str,
        width: int,
        height: int,
        size_bytes: int,
        storage_key: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> Asset: ...

    async def get(
        self,
        *,
        asset_id: UUID,
        scope: ConversationScope,
    ) -> Asset | None: ...

    async def list_for_message(
        self,
        *,
        message_id: UUID,
        scope: ConversationScope,
    ) -> Sequence[Asset] | None: ...


class AssetStorage(Protocol):
    async def is_available(self) -> bool: ...

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    async def get(self, *, key: str) -> bytes: ...

    async def delete(self, *, key: str) -> None: ...
