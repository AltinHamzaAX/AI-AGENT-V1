"""Compatibility exports for object-storage application ports."""

from app.modules.posts.providers import StorageProvider

ObjectStorage = StorageProvider

__all__ = ["ObjectStorage", "StorageProvider"]
