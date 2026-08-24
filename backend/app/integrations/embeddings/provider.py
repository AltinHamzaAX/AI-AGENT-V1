from app.core.config import Settings
from app.integrations.provider_factory import create_embedding_provider as _create
from app.modules.posts.providers import EmbeddingProvider


def create_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    return _create(settings)


__all__ = ["create_embedding_provider"]
