from app.core.config import Settings, get_settings
from app.infrastructure.storage.s3 import S3Storage
from app.integrations.gemini import GeminiProvider
from app.integrations.huggingface import HuggingFaceImageProvider
from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.integrations.ollama import (
    OllamaEmbeddingProvider,
    OllamaLLMProvider,
    OllamaVisionProvider,
)
from app.integrations.tavily import TavilyResearchProvider
from app.modules.posts.providers import (
    EmbeddingProvider,
    ImageProvider,
    LLMProvider,
    ProviderBundle,
    ProviderConfigurationError,
    ResearchProvider,
    StorageProvider,
    VisionProvider,
)


def create_provider_bundle(settings: Settings | None = None) -> ProviderBundle:
    configured = settings or get_settings()
    names = {
        "llm": _name(configured.llm_provider, capability="LLM"),
        "vision": _name(configured.vision_provider, capability="vision"),
        "image": _name(configured.image_provider, capability="image"),
        "embedding": _name(configured.embedding_provider, capability="embedding"),
        "research": _name(configured.research_provider, capability="research"),
        "storage": _name(configured.storage_provider, capability="storage"),
    }
    # Traces name the model from the response itself, so a separate creative
    # model needs no entry here: `names` maps capability to provider.
    return ProviderBundle(
        llm=create_llm_provider(configured),
        creative_llm_override=create_creative_llm_provider(configured),
        vision=create_vision_provider(configured),
        image=create_image_provider(configured),
        embedding=create_embedding_provider(configured),
        research=create_research_provider(configured),
        storage=create_storage_provider(configured),
        names=names,
    )


def create_llm_provider(settings: Settings | None = None) -> LLMProvider:
    configured = settings or get_settings()
    name = _name(configured.llm_provider, capability="LLM")
    if name == "mock":
        return MockLLMProvider()
    if name == "gemini":
        return GeminiProvider(
            api_key=_gemini_api_key(configured),
            model=_model(configured.llm_model, capability="LLM"),
            timeout_seconds=configured.provider_timeout_seconds,
        )
    if name == "ollama":
        return OllamaLLMProvider(
            base_url=configured.ollama_base_url,
            model=_model(configured.llm_model, capability="LLM"),
            timeout_seconds=configured.provider_timeout_seconds,
            num_predict=configured.ollama_llm_num_predict,
            keep_alive=configured.ollama_keep_alive,
        )
    raise _unsupported("LLM", name)


def create_creative_llm_provider(settings: Settings | None = None) -> LLMProvider | None:
    """The stronger model for inventive stages, or None when none is configured.

    None rather than a duplicate of the default provider, so the bundle can say
    truthfully whether this deployment separates the two.
    """
    configured = settings or get_settings()
    model = configured.creative_llm_model.strip()
    if not model or (
        model == configured.llm_model.strip()
        and configured.ollama_creative_num_predict == configured.ollama_llm_num_predict
    ):
        return None
    name = _name(configured.llm_provider, capability="LLM")
    if name == "mock":
        return MockLLMProvider()
    if name == "gemini":
        return GeminiProvider(
            api_key=_gemini_api_key(configured),
            model=model,
            timeout_seconds=configured.provider_timeout_seconds,
        )
    if name == "ollama":
        return OllamaLLMProvider(
            base_url=configured.ollama_base_url,
            model=model,
            timeout_seconds=configured.provider_timeout_seconds,
            num_predict=configured.ollama_creative_num_predict,
            keep_alive=configured.ollama_keep_alive,
        )
    raise _unsupported("LLM", name)


def create_vision_provider(settings: Settings | None = None) -> VisionProvider:
    configured = settings or get_settings()
    name = _name(configured.vision_provider, capability="vision")
    if name == "mock":
        return MockVisionProvider()
    if name == "ollama":
        return OllamaVisionProvider(
            base_url=configured.ollama_base_url,
            model=_model(configured.vision_model, capability="vision"),
            timeout_seconds=configured.provider_timeout_seconds,
            num_predict=configured.ollama_vision_num_predict,
            keep_alive=configured.ollama_keep_alive,
        )
    raise _unsupported("vision", name)


def create_image_provider(settings: Settings | None = None) -> ImageProvider:
    configured = settings or get_settings()
    name = _name(configured.image_provider, capability="image")
    if name == "mock":
        return MockImageProvider()
    if name in {"huggingface", "hf"}:
        if not configured.huggingface_api_token:
            raise ProviderConfigurationError("Hugging Face token is required")
        return HuggingFaceImageProvider(
            token=configured.huggingface_api_token,
            model=_model(configured.image_model, capability="image"),
        )
    raise _unsupported("image", name)


def create_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    configured = settings or get_settings()
    name = _name(configured.embedding_provider, capability="embedding")
    if name == "mock":
        return MockEmbeddingProvider()
    if name == "ollama":
        return OllamaEmbeddingProvider(
            base_url=configured.ollama_base_url,
            model=_model(configured.embedding_model, capability="embedding"),
            timeout_seconds=configured.provider_timeout_seconds,
            keep_alive=configured.ollama_keep_alive,
        )
    raise _unsupported("embedding", name)


def create_research_provider(settings: Settings | None = None) -> ResearchProvider:
    configured = settings or get_settings()
    name = _name(configured.research_provider, capability="research")
    if name == "mock":
        return MockResearchProvider()
    if name == "tavily":
        if not configured.tavily_api_key:
            raise ProviderConfigurationError("Tavily API key is required")
        return TavilyResearchProvider(
            api_key=configured.tavily_api_key,
            base_url=configured.tavily_api_base_url,
            timeout_seconds=min(configured.provider_timeout_seconds, 60.0),
        )
    raise _unsupported("research", name)


def create_storage_provider(settings: Settings | None = None) -> StorageProvider:
    configured = settings or get_settings()
    name = _name(configured.storage_provider, capability="storage")
    if name == "mock":
        return MockStorageProvider()
    if name in {"s3", "minio"}:
        return S3Storage(
            bucket=configured.s3_bucket,
            endpoint_url=configured.s3_endpoint,
            access_key=configured.s3_access_key,
            secret_key=configured.s3_secret_key,
        )
    raise _unsupported("storage", name)


def _name(value: str, *, capability: str) -> str:
    name = value.strip().lower()
    if not name:
        raise ProviderConfigurationError(f"{capability} provider is not configured")
    return name


def _model(value: str, *, capability: str) -> str:
    model = value.strip()
    if not model:
        raise ProviderConfigurationError(f"{capability} model is not configured")
    return model


def _gemini_api_key(settings: Settings) -> str:
    if not settings.gemini_api_key:
        raise ProviderConfigurationError("Gemini API key is required")
    return settings.gemini_api_key


def _unsupported(capability: str, provider: str) -> ProviderConfigurationError:
    return ProviderConfigurationError(f"Unsupported {capability} provider: {provider}")


__all__ = [
    "create_creative_llm_provider",
    "create_embedding_provider",
    "create_image_provider",
    "create_llm_provider",
    "create_provider_bundle",
    "create_research_provider",
    "create_storage_provider",
    "create_vision_provider",
]
