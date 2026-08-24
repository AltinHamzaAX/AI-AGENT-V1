"""Compatibility export for the object-storage application port."""

from app.shared.assets.contracts import AssetStorage

ObjectStorage = AssetStorage

__all__ = ["ObjectStorage"]
