import asyncio
from io import BytesIO
from typing import Any

import httpx
from huggingface_hub import InferenceClient

from app.integrations.http import ProviderHTTPAdapter
from app.modules.posts.providers import (
    ImageRequest,
    ImageResponse,
    LLMRequest,
    LLMResponse,
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
)


class HuggingFaceLLMProvider:
    provider_name = "huggingface"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://router.huggingface.co/v1",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key.strip():
            raise ProviderConfigurationError("Hugging Face API key is required")
        if not model.strip():
            raise ProviderConfigurationError("Hugging Face model is required")
        if not base_url.strip():
            raise ProviderConfigurationError("Hugging Face base URL is required")
        self._api_key = api_key
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._http = ProviderHTTPAdapter(
            client=client,
            timeout_seconds=timeout_seconds,
            error_mapper=_map_huggingface_http_error,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = await self._http.post_json(
            provider=self.provider_name,
            url=f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=_llm_request_payload(request, model=self._model),
        )
        text = _llm_response_text(body)
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            text=text,
            provider=self.provider_name,
            model=self._model,
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
        )


def _llm_request_payload(request: LLMRequest, *, model: str) -> dict[str, Any]:
    if not request.messages:
        raise ValueError("LLM request requires at least one message")
    messages: list[dict[str, str]] = []
    for message in request.messages:
        if not message.content.strip():
            raise ValueError("LLM messages cannot be empty")
        role = "assistant" if message.role == "model" else message.role
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"Unsupported LLM message role: {message.role}")
        messages.append({"role": role, "content": message.content})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": request.temperature,
    }
    if request.response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    elif request.response_format is not None:
        raise ValueError(f"Unsupported LLM response format: {request.response_format}")
    return payload


def _llm_response_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError("huggingface returned no response choices")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise ProviderResponseError("huggingface returned an unusable text response")
    text = text.strip()
    if not text:
        raise ProviderResponseError("huggingface returned an empty text response")
    return text


def _map_huggingface_http_error(response: httpx.Response) -> ProviderError | None:
    status = response.status_code
    if status in {401, 403}:
        return ProviderConfigurationError("huggingface authentication failed")
    if status == 404:
        return ProviderConfigurationError("huggingface model is unavailable")
    if status == 429:
        if _is_quota_error(response):
            return ProviderQuotaError("huggingface usage allowance is exhausted")
        return ProviderRateLimitError("huggingface rate limit reached")
    return None


def _is_quota_error(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if isinstance(error, dict):
        values = (error.get("type"), error.get("code"), error.get("message"))
    else:
        values = (error,)
    classification = " ".join(value.lower() for value in values if isinstance(value, str))
    return any(marker in classification for marker in ("quota", "credit", "billing"))


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


class HuggingFaceImageProvider:
    provider_name = "huggingface"

    def __init__(
        self,
        *,
        token: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client or InferenceClient(provider="auto", api_key=token)

    async def generate(self, request: ImageRequest) -> ImageResponse:
        if not request.prompt.strip():
            raise ValueError("Image prompt cannot be empty")
        parameters = {
            key: value
            for key, value in {
                "negative_prompt": request.negative_prompt,
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            }.items()
            if value is not None
        }
        try:
            image = await asyncio.to_thread(
                self._client.text_to_image,
                request.prompt,
                model=self._model,
                **parameters,
            )
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        except Exception as exc:  # noqa: BLE001 - provider SDK boundary
            raise ProviderError("huggingface image request failed") from exc
        data = buffer.getvalue()
        if not data:
            raise ProviderError("huggingface returned an empty image")
        return ImageResponse(
            image=data,
            mime_type="image/png",
            provider=self.provider_name,
            model=self._model,
        )


__all__ = ["HuggingFaceImageProvider", "HuggingFaceLLMProvider"]
