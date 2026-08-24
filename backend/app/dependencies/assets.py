from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infrastructure.database.repositories.assets import SQLAlchemyAssetRepository
from app.infrastructure.database.session import get_db_transaction
from app.integrations.provider_factory import create_storage_provider
from app.shared.assets.contracts import AssetStorage
from app.shared.assets.service import AssetService


def get_asset_storage() -> AssetStorage:
    return create_storage_provider(get_settings())


def get_asset_service(
    session: Annotated[AsyncSession, Depends(get_db_transaction)],
    storage: Annotated[AssetStorage, Depends(get_asset_storage)],
) -> AssetService:
    settings = get_settings()
    return AssetService(
        repository=SQLAlchemyAssetRepository(session),
        storage=storage,
        max_size_bytes=settings.asset_max_size_bytes,
        max_dimension=settings.asset_max_dimension,
        max_pixels=settings.asset_max_pixels,
    )


AssetServiceDependency = Annotated[AssetService, Depends(get_asset_service)]
