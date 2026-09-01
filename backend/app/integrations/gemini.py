from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.http import ProviderHTTPAdapter
from app.integrations.llm import (
    LLMRequest,
    LLMResponse,
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
)


class GeminiProvider:
    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key.strip():
            raise ProviderConfigurationError("Gemini API key is required")
        if not model.strip():
            raise ProviderConfigurationError("Gemini model is required")
        self._api_key = api_key
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._http = ProviderHTTPAdapter(
            client=client,
            timeout_seconds=timeout_seconds,
            error_mapper=_map_gemini_http_error,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = _request_payload(request)
        model_path = quote(self._model, safe="")
        body = await self._http.post_json(
            provider=self.provider_name,
            url=f"{self._base_url}/v1beta/models/{model_path}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            payload=payload,
        )
        text = _response_text(body)
        usage = body.get("usageMetadata")
        usage = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            text=text,
            provider=self.provider_name,
            model=self._model,
            input_tokens=_optional_int(usage.get("promptTokenCount")),
            output_tokens=_optional_int(usage.get("candidatesTokenCount")),
        )


def _request_payload(request: LLMRequest) -> dict[str, Any]:
    if not request.messages:
        raise ValueError("LLM request requires at least one message")
    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    for message in request.messages:
        if not message.content.strip():
            raise ValueError("LLM messages cannot be empty")
        if message.role == "system":
            system_parts.append({"text": message.content})
            continue
        role = "model" if message.role in {"assistant", "model"} else message.role
        if role != "user" and role != "model":
            raise ValueError(f"Unsupported LLM message role: {message.role}")
        contents.append({"role": role, "parts": [{"text": message.content}]})
    if not contents:
        raise ValueError("LLM request requires at least one user or assistant message")

    generation_config: dict[str, Any] = {"temperature": request.temperature}
    if request.response_format == "json":
        generation_config["responseMimeType"] = "application/json"
    elif request.response_format is not None:
        raise ValueError(f"Unsupported LLM response format: {request.response_format}")

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    return payload


def _response_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProviderResponseError("gemini returned no response candidates")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ProviderResponseError("gemini returned an invalid response candidate")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ProviderResponseError("gemini returned an invalid content response")
    text_parts = [part.get("text") for part in parts if isinstance(part, dict)]
    if not text_parts or any(not isinstance(text, str) for text in text_parts):
        raise ProviderResponseError("gemini returned an unusable text response")
    text = "".join(text_parts).strip()
    if not text:
        raise ProviderResponseError("gemini returned an empty text response")
    return text


def _map_gemini_http_error(response: httpx.Response) -> ProviderError | None:
    status = response.status_code
    error_type, error_reasons = _error_classification(response)
    if status in {401, 403} or "API_KEY_INVALID" in error_reasons:
        return ProviderConfigurationError("gemini authentication failed")
    if status == 404:
        return ProviderConfigurationError("gemini model is unavailable")
    if status == 429:
        if error_type == "quota_exceeded":
            return ProviderQuotaError("gemini usage allowance is exhausted")
        return ProviderRateLimitError("gemini rate limit reached")
    return None


def _error_classification(response: httpx.Response) -> tuple[str | None, set[str]]:
    try:
        body = response.json()
    except ValueError:
        return None, set()
    if not isinstance(body, dict):
        return None, set()
    error = body.get("error")
    if not isinstance(error, dict):
        return None, set()
    value = error.get("type")
    error_type = value if isinstance(value, str) else None
    details = error.get("details")
    details = details if isinstance(details, list) else []
    reasons = {
        reason
        for detail in details
        if isinstance(detail, dict)
        if isinstance((reason := detail.get("reason")), str)
    }
    return error_type, reasons


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


__all__ = ["GeminiProvider"]
