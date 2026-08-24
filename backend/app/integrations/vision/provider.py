from app.core.config import Settings
from app.integrations.provider_factory import create_vision_provider as _create
from app.modules.posts.providers import VisionProvider


def create_vision_provider(settings: Settings | None = None) -> VisionProvider:
    return _create(settings)


__all__ = ["create_vision_provider"]
