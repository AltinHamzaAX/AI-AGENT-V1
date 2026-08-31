from dataclasses import FrozenInstanceError

import pytest

from app.core.config import Settings
from app.integrations.llm import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from app.modules.posts.providers import (
    LLMMessage as ExistingLLMMessage,
    LLMProvider as ExistingLLMProvider,
    LLMRequest as ExistingLLMRequest,
    LLMResponse as ExistingLLMResponse,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_password": "test",
        "database_url": "sqlite+aiosqlite://",
        "redis_url": "redis://localhost:6379/0",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "test",
        "s3_secret_key": "test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_campaign_llm_boundary_reuses_existing_contracts() -> None:
    assert LLMMessage is ExistingLLMMessage
    assert LLMProvider is ExistingLLMProvider
    assert LLMRequest is ExistingLLMRequest
    assert LLMResponse is ExistingLLMResponse


@pytest.mark.asyncio
async def test_provider_contract_accepts_provider_neutral_requests_and_responses() -> None:
    class StubProvider:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                text=request.messages[-1].content,
                provider="stub",
                model="test-model",
            )

    provider: LLMProvider = StubProvider()
    request = LLMRequest(
        messages=(
            LLMMessage(role="system", content="Respond concisely"),
            LLMMessage(role="user", content="Build a campaign"),
        ),
        response_format="json",
    )

    response = await provider.complete(request)

    assert response.text == "Build a campaign"
    assert response.provider == "stub"
    assert response.model == "test-model"


def test_llm_request_and_response_are_immutable_value_contracts() -> None:
    request = LLMRequest(messages=(LLMMessage(role="user", content="Brief"),))
    response = LLMResponse(text="Answer", provider="stub", model="test-model")

    with pytest.raises(FrozenInstanceError):
        request.temperature = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        response.text = "Changed"  # type: ignore[misc]


def test_llm_provider_and_model_follow_backend_settings() -> None:
    settings = _settings(llm_provider="gemini", llm_model="configured-model")

    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "configured-model"
