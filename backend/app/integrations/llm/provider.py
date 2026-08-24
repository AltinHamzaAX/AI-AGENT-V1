from app.core.config import Settings
from app.integrations.provider_factory import create_llm_provider as _create
from app.modules.posts.providers import LLMProvider


def create_llm_provider(settings: Settings | None = None) -> LLMProvider:
    return _create(settings)
