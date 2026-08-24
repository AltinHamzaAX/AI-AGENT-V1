import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.core.config import Settings
from app.infrastructure.storage.s3 import S3Storage
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
from app.integrations.provider_factory import create_provider_bundle
from app.integrations.tavily import TavilyResearchProvider
from app.modules.posts.providers import (
    EmbeddingRequest,
    ImageRequest,
    LLMMessage,
    LLMRequest,
    ProviderConfigurationError,
    ProviderError,
    ResearchRequest,
    VisionRequest,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_password": "test",
        "database_url": "sqlite+aiosqlite://",
        "redis_url": "redis://localhost:6379/0",
        "storage_provider": "mock",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "test",
        "s3_secret_key": "test",
        "llm_provider": "mock",
        "llm_model": "mock",
        "image_provider": "mock",
        "image_model": "mock",
        "vision_provider": "mock",
        "vision_model": "mock",
        "embedding_provider": "mock",
        "embedding_model": "mock",
        "research_provider": "mock",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_config_can_switch_every_capability_to_mock_provider() -> None:
    providers = create_provider_bundle(_settings())

    assert isinstance(providers.llm, MockLLMProvider)
    assert isinstance(providers.vision, MockVisionProvider)
    assert isinstance(providers.image, MockImageProvider)
    assert isinstance(providers.embedding, MockEmbeddingProvider)
    assert isinstance(providers.research, MockResearchProvider)
    assert isinstance(providers.storage, MockStorageProvider)
    assert providers.names == {
        "llm": "mock",
        "vision": "mock",
        "image": "mock",
        "embedding": "mock",
        "research": "mock",
        "storage": "mock",
    }

    llm = await providers.llm.complete(
        LLMRequest(messages=(LLMMessage(role="user", content="hello"),))
    )
    vision = await providers.vision.analyze(
        VisionRequest(image=b"image", mime_type="image/png", prompt="describe")
    )
    image = await providers.image.generate(ImageRequest(prompt="scene"))
    embeddings = await providers.embedding.embed(EmbeddingRequest(texts=("one", "two")))
    research = await providers.research.search(ResearchRequest(query="market"))

    assert llm.text == "hello" and llm.provider == "mock"
    assert vision.data["size_bytes"] == 5
    assert image.image.startswith(b"\x89PNG")
    assert len(embeddings.vectors) == 2 and embeddings.dimension == 8
    assert research.results[0].url == "https://example.test/research"


def test_unknown_or_missing_provider_fails_closed_without_secret_leakage() -> None:
    with pytest.raises(ProviderConfigurationError, match="Unsupported LLM provider: unknown"):
        create_provider_bundle(_settings(llm_provider="unknown"))
    with pytest.raises(ProviderConfigurationError, match="LLM provider is not configured"):
        create_provider_bundle(_settings(llm_provider=""))


def test_config_selects_concrete_adapters_without_changing_contracts() -> None:
    providers = create_provider_bundle(
        _settings(
            llm_provider="ollama",
            llm_model="qwen",
            vision_provider="ollama",
            vision_model="qwen-vl",
            image_provider="huggingface",
            image_model="flux",
            huggingface_api_token="hf-test",
            embedding_provider="ollama",
            embedding_model="embeddinggemma",
            research_provider="tavily",
            tavily_api_key="tvly-test",
            storage_provider="s3",
        )
    )

    assert isinstance(providers.llm, OllamaLLMProvider)
    assert isinstance(providers.vision, OllamaVisionProvider)
    assert isinstance(providers.image, HuggingFaceImageProvider)
    assert isinstance(providers.embedding, OllamaEmbeddingProvider)
    assert isinstance(providers.research, TavilyResearchProvider)
    assert isinstance(providers.storage, S3Storage)


@pytest.mark.asyncio
async def test_ollama_adapters_use_provider_neutral_contracts() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path == "/api/embed":
            return httpx.Response(
                200,
                json={"model": "embeddinggemma", "embeddings": [[0.1, 0.2], [0.3, 0.4]]},
            )
        content = (
            '{"objects": ["product"]}'
            if payload["messages"][0].get("images")
            else "completed"
        )
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "message": {"role": "assistant", "content": content},
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OllamaLLMProvider(
            base_url="http://ollama.test", model="qwen", client=client
        )
        vision = OllamaVisionProvider(
            base_url="http://ollama.test", model="qwen-vl", client=client
        )
        embedding = OllamaEmbeddingProvider(
            base_url="http://ollama.test", model="embeddinggemma", client=client
        )
        llm_response = await llm.complete(
            LLMRequest(messages=(LLMMessage(role="user", content="brief"),))
        )
        vision_response = await vision.analyze(
            VisionRequest(image=b"binary", mime_type="image/png", prompt="analyze")
        )
        embedding_response = await embedding.embed(
            EmbeddingRequest(texts=("one", "two"))
        )

    assert llm_response.text == "completed"
    assert llm_response.input_tokens == 4 and llm_response.output_tokens == 2
    assert vision_response.data == {"objects": ["product"]}
    assert requests[1]["messages"][0]["images"] == [
        base64.b64encode(b"binary").decode("ascii")
    ]
    assert embedding_response.vectors == ((0.1, 0.2), (0.3, 0.4))


@pytest.mark.asyncio
async def test_tavily_adapter_maps_results_and_uses_bearer_auth() -> None:
    captured_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_authorization
        captured_authorization = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "query": "Kosovo rentals",
                "answer": "Demand is seasonal.",
                "results": [
                    {
                        "title": "Market source",
                        "url": "https://example.test/source",
                        "content": "Source-aware evidence.",
                        "score": 0.9,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-secret", client=client)
        response = await provider.search(
            ResearchRequest(query="Kosovo rentals", max_results=3)
        )

    assert captured_authorization == "Bearer tvly-secret"
    assert response.answer == "Demand is seasonal."
    assert response.results[0].score == 0.9


@pytest.mark.asyncio
async def test_http_provider_error_does_not_expose_response_or_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="tvly-secret internal response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyResearchProvider(api_key="tvly-secret", client=client)
        with pytest.raises(ProviderError) as captured:
            await provider.search(ResearchRequest(query="market"))

    assert "tvly-secret" not in str(captured.value)
    assert "internal response" not in str(captured.value)
    assert "status 401" in str(captured.value)


class _FakeInferenceClient:
    def __init__(self) -> None:
        self.call: tuple[str, str, dict[str, object]] | None = None

    def text_to_image(self, prompt: str, *, model: str, **parameters: object) -> Image.Image:
        self.call = (prompt, model, parameters)
        return Image.new("RGB", (2, 2), "blue")


@pytest.mark.asyncio
async def test_huggingface_image_adapter_returns_provider_neutral_png() -> None:
    client = _FakeInferenceClient()
    provider = HuggingFaceImageProvider(token="hf-secret", model="flux", client=client)
    response = await provider.generate(
        ImageRequest(
            prompt="studio product scene",
            negative_prompt="text, watermark",
            width=1024,
            height=1024,
            seed=7,
        )
    )

    assert response.mime_type == "image/png"
    assert response.image.startswith(b"\x89PNG")
    assert client.call == (
        "studio product scene",
        "flux",
        {
            "negative_prompt": "text, watermark",
            "width": 1024,
            "height": 1024,
            "seed": 7,
        },
    )


@pytest.mark.asyncio
async def test_mock_image_is_decodable() -> None:
    provider = MockImageProvider()
    response = await provider.generate(ImageRequest(prompt="scene"))
    with Image.open(BytesIO(response.image)) as image:
        assert image.size == (1, 1)
