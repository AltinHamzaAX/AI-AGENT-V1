from collections.abc import Callable
from typing import Any

import httpx

from app.modules.posts.providers import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
)

#: 402 is the conventional "payment required"; Tavily answers 432 for a spent
#: plan allowance. Both mean the same thing to a caller: stop asking.
_QUOTA_STATUSES = frozenset({402, 432})
_RATE_LIMIT_STATUS = 429
HTTPErrorMapper = Callable[[httpx.Response], ProviderError | None]


class ProviderHTTPAdapter:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
        error_mapper: HTTPErrorMapper | None = None,
    ) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._error_mapper = error_mapper

    async def post_json(
        self,
        *,
        provider: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._post(
            provider=provider,
            url=url,
            payload=payload,
            headers=headers,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"{provider} returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderResponseError(f"{provider} returned an invalid response shape")
        return body

    async def post_bytes(
        self,
        *,
        provider: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[bytes, str]:
        response = await self._post(
            provider=provider,
            url=url,
            payload=payload,
            headers=headers,
        )
        content_type = response.headers.get("content-type", "application/octet-stream")
        if not response.content:
            raise ProviderResponseError(f"{provider} returned an empty response")
        return response.content, content_type.split(";", maxsplit=1)[0]

    async def _post(
        self,
        *,
        provider: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{provider} request timed out") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if self._error_mapper is not None:
                mapped = self._error_mapper(exc.response)
                if mapped is not None:
                    raise mapped from exc
            if status in _QUOTA_STATUSES:
                raise ProviderQuotaError(
                    f"{provider} usage allowance is exhausted (status {status})"
                ) from exc
            if status == _RATE_LIMIT_STATUS:
                raise ProviderRateLimitError(
                    f"{provider} rate limit reached (status {status})"
                ) from exc
            raise ProviderError(f"{provider} request failed with status {status}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{provider} request failed") from exc


__all__ = ["HTTPErrorMapper", "ProviderHTTPAdapter"]
