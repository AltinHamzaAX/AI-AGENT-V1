from app.core.config import Settings
from app.integrations.provider_factory import create_image_provider as _create
from app.modules.posts.providers import ImageProvider


def create_image_provider(settings: Settings | None = None) -> ImageProvider:
    return _create(settings)
