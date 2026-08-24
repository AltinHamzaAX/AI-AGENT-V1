from app.core.config import Settings
from app.integrations.provider_factory import create_storage_provider as _create
from app.modules.posts.providers import StorageProvider


def create_storage_provider(settings: Settings | None = None) -> StorageProvider:
    return _create(settings)


__all__ = ["create_storage_provider"]
