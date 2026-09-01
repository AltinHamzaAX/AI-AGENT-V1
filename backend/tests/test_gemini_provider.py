import json

import httpx
import pytest

from app.core.config import Settings
from app.integrations.gemini import GeminiProvider
from app.integrations.provider_factory import create_llm_provider
from app.modules.posts.providers import (
    LLMMessage,
    LLMProvider,
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
        "llm_provider": "gemini",
        "llm_model": "configured-model",
        "gemini_api_key": "test-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _response_body(*, text: str = "completed") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
    }


@pytest.mark.asyncio
async def test_gemini_converts_provider_neutral_request_and_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider: LLMProvider = GeminiProvider(
            api_key="test-secret",
            model="configured-model",
            base_url="https://gemini.test",
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
            )
        )

    payload = json.loads(requests[0].content)
    assert requests[0].url.path.endswith("/models/configured-model:generateContent")
    assert requests[0].headers["x-goog-api-key"] == "test-secret"
    assert payload == {
        "contents": [
            {"role": "user", "parts": [{"text": "Campaign brief"}]},
            {"role": "model", "parts": [{"text": "Prior answer"}]},
        ],
        "generationConfig": {"temperature": 0.4},
        "systemInstruction": {"parts": [{"text": "System instructions"}]},
    }
    assert response.text == "completed"
    assert response.provider == "gemini"
    assert response.model == "configured-model"
    assert response.input_tokens == 7
    assert response.output_tokens == 3


@pytest.mark.asyncio
async def test_gemini_requests_json_output_through_neutral_response_format() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_response_body(text='{"reply":"Hello"}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider(api_key="test-secret", model="model", client=client)
        response = await provider.complete(
            LLMRequest(
                messages=(LLMMessage(role="user", content="Return JSON"),),
                response_format="json",
            )
        )

    assert payloads[0]["generationConfig"]["responseMimeType"] == "application/json"
    assert json.loads(response.text) == {"reply": "Hello"}


def test_factory_selects_gemini_and_requires_backend_configuration() -> None:
    provider = create_llm_provider(_settings())

    assert isinstance(provider, GeminiProvider)
    assert provider._model == "configured-model"
    assert "test-secret" not in repr(_settings())
    with pytest.raises(ProviderConfigurationError, match="Gemini API key is required"):
        create_llm_provider(_settings(gemini_api_key=""))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (
            400,
            {"error": {"details": [{"reason": "API_KEY_INVALID"}]}},
            ProviderConfigurationError,
        ),
        (401, {}, ProviderConfigurationError),
        (403, {}, ProviderConfigurationError),
        (429, {"error": {"type": "rate_limit_exceeded"}}, ProviderRateLimitError),
        (429, {"error": {"type": "quota_exceeded"}}, ProviderQuotaError),
        (503, {}, ProviderError),
    ],
)
async def test_gemini_maps_provider_failures_without_exposing_secrets(
    status: int,
    body: dict,
    error_type: type[ProviderError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider(api_key="test-secret", model="model", client=client)
        with pytest.raises(error_type) as captured:
            await provider.complete(
                LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
            )

    assert "test-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_gemini_maps_timeout_and_malformed_response() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        provider = GeminiProvider(api_key="test-secret", model="model", client=client)
        with pytest.raises(ProviderError, match="gemini request timed out"):
            await provider.complete(
                LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
            )

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed_handler)) as client:
        provider = GeminiProvider(api_key="test-secret", model="model", client=client)
        with pytest.raises(ProviderResponseError, match="no response candidates"):
            await provider.complete(
                LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
            )
