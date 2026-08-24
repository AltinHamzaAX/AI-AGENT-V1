from app.integrations.image_generation.base import ImageGenerationProvider


def create_image_provider() -> ImageGenerationProvider:
    raise NotImplementedError("No image provider is configured")
