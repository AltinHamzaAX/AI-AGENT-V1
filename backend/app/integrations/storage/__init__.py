"""Storage provider integration boundary."""
from app.integrations.storage.provider import create_storage_provider
from app.modules.posts.providers import StorageProvider

__all__ = ["StorageProvider", "create_storage_provider"]
