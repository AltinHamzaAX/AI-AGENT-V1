from functools import lru_cache

from app.integrations.llm import LLMProvider
from app.integrations.provider_factory import create_llm_provider, create_provider_bundle
from app.modules.posts.providers import ProviderBundle


@lru_cache
def get_llm_provider() -> LLMProvider:
    """Resolve only the LLM capability for consumers that need nothing else."""
    return create_llm_provider()


@lru_cache
def get_provider_bundle() -> ProviderBundle:
    """Composition root used by workers and future Posts orchestration."""
    return create_provider_bundle()


__all__ = ["get_llm_provider", "get_provider_bundle"]
