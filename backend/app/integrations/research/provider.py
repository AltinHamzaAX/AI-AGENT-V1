from app.core.config import Settings
from app.integrations.provider_factory import create_research_provider as _create
from app.modules.posts.providers import ResearchProvider


def create_research_provider(settings: Settings | None = None) -> ResearchProvider:
    return _create(settings)


__all__ = ["create_research_provider"]
