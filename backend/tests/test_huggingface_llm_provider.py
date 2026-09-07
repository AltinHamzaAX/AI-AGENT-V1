import json

import httpx
import pytest

from app.core.config import Settings
from app.integrations.huggingface import HuggingFaceLLMProvider
from app.integrations.provider_factory import create_llm_provider
from app.modules.posts.providers import (
    LLMMessage,
    LLMRequest,
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
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
        "llm_provider": "huggingface",
        "llm_model": "Qwen/Qwen2.5-7B-Instruct-1M",
        "huggingface_api_key": "test-secret",
        "huggingface_base_url": "https://router.huggingface.test/v1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _response_body(*, text: str = "completed") -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def test_factory_selects_huggingface_and_requires_configuration() -> None:
    provider = create_llm_provider(_settings())

    assert isinstance(provider, HuggingFaceLLMProvider)
    assert provider._model == "Qwen/Qwen2.5-7B-Instruct-1M"
    assert provider._base_url == "https://router.huggingface.test/v1"
    assert "test-secret" not in repr(_settings())
    with pytest.raises(ProviderConfigurationError, match="Hugging Face API key is required"):
        create_llm_provider(_settings(huggingface_api_key=""))


@pytest.mark.asyncio
async def test_huggingface_converts_provider_neutral_request_and_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HuggingFaceLLMProvider(
            api_key="test-secret",
            model="configured-model",
            base_url="https://router.huggingface.test/v1",
            client=client,
        )
        response = await provider.complete(
            LLMRequest(
                messages=(
                    LLMMessage(role="system", content="System instructions"),
                    LLMMessage(role="user", content="Campaign brief"),
                    LLMMessage(role="assistant", content="Prior answer"),
                ),
                temperature=0.4,
                response_format="json",
            )
        )

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer test-secret"
    assert payload == {
        "model": "configured-model",
        "messages": [
            {"role": "system", "content": "System instructions"},
            {"role": "user", "content": "Campaign brief"},
            {"role": "assistant", "content": "Prior answer"},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    assert response.text == "completed"
    assert response.provider == "huggingface"
    assert response.model == "configured-model"
    assert response.input_tokens == 7
    assert response.output_tokens == 3


@pytest.mark.asyncio
async def test_huggingface_rejects_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HuggingFaceLLMProvider(api_key="test-secret", model="model", client=client)
        with pytest.raises(ProviderResponseError, match="no response choices"):
            await provider.complete(
                LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (401, {"error": "invalid token"}, ProviderConfigurationError),
        (403, {"error": "forbidden"}, ProviderConfigurationError),
        (429, {"error": {"type": "rate_limit_exceeded"}}, ProviderRateLimitError),
        (429, {"error": {"type": "quota_exceeded"}}, ProviderQuotaError),
        (503, {"error": "internal provider detail"}, ProviderError),
    ],
)
async def test_huggingface_maps_failures_without_exposing_response_or_secret(
    status: int,
    body: dict,
    error_type: type[ProviderError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HuggingFaceLLMProvider(api_key="test-secret", model="model", client=client)
        with pytest.raises(error_type) as captured:
            await provider.complete(
                LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
            )

    message = str(captured.value)
    assert "test-secret" not in message
    assert "internal provider detail" not in message
