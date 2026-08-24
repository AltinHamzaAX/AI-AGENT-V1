from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assets import AssetModel
from app.models.conversations import ConversationModel, MessageModel
from app.shared.assets.domain import Asset, AssetRole
from app.shared.conversations.domain import ConversationScope


def _asset(model: AssetModel) -> Asset:
    return Asset(
        id=model.id,
        scope=ConversationScope(user_id=model.user_id, project_id=model.project_id),
        message_id=model.message_id,
        role=AssetRole(model.role),
        original_filename=model.original_filename,
        mime_type=model.mime_type,
        width=model.width,
        height=model.height,
        size_bytes=model.size_bytes,
        storage_key=model.storage_key,
        checksum=model.checksum,
        metadata=dict(model.asset_metadata),
        created_at=model.created_at,
    )


class SQLAlchemyAssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def message_exists(
        self,
        *,
        message_id: UUID,
        scope: ConversationScope,
    ) -> bool:
        statement = (
            select(MessageModel.id)
            .join(ConversationModel, ConversationModel.id == MessageModel.conversation_id)
            .where(
                MessageModel.id == message_id,
                ConversationModel.user_id == scope.user_id,
                ConversationModel.project_id == scope.project_id,
            )
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def find_by_checksum(
        self,
        *,
        scope: ConversationScope,
        checksum: str,
    ) -> Asset | None:
        statement = (
            select(AssetModel)
            .where(
                AssetModel.user_id == scope.user_id,
                AssetModel.project_id == scope.project_id,
                AssetModel.checksum == checksum,
            )
            .order_by(AssetModel.created_at, AssetModel.id)
            .limit(1)
        )
        model = (await self._session.execute(statement)).scalar_one_or_none()
        return _asset(model) if model else None

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
    ) -> Asset:
        model = AssetModel(
            id=asset_id,
            user_id=scope.user_id,
            project_id=scope.project_id,
            message_id=message_id,
            role=role.value,
            original_filename=original_filename,
            mime_type=mime_type,
            width=width,
            height=height,
            size_bytes=size_bytes,
            storage_key=storage_key,
            checksum=checksum,
            asset_metadata=metadata,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _asset(model)

    async def get(
        self,
        *,
        asset_id: UUID,
        scope: ConversationScope,
    ) -> Asset | None:
        statement = select(AssetModel).where(
            AssetModel.id == asset_id,
            AssetModel.user_id == scope.user_id,
            AssetModel.project_id == scope.project_id,
        )
        model = (await self._session.execute(statement)).scalar_one_or_none()
        return _asset(model) if model else None

    async def list_for_message(
        self,
        *,
        message_id: UUID,
        scope: ConversationScope,
    ) -> Sequence[Asset] | None:
        if not await self.message_exists(message_id=message_id, scope=scope):
            return None
        statement = (
            select(AssetModel)
            .where(
                AssetModel.message_id == message_id,
                AssetModel.user_id == scope.user_id,
                AssetModel.project_id == scope.project_id,
            )
            .order_by(AssetModel.created_at, AssetModel.id)
        )
        models = (await self._session.execute(statement)).scalars().all()
        return tuple(_asset(model) for model in models)
