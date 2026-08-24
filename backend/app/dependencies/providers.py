from functools import lru_cache

from app.integrations.provider_factory import create_provider_bundle
from app.modules.posts.providers import ProviderBundle


@lru_cache
def get_provider_bundle() -> ProviderBundle:
    """Composition root used by workers and future Posts orchestration."""
    return create_provider_bundle()


__all__ = ["get_provider_bundle"]
