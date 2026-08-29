import base64
import json
from typing import Any

import httpx

from app.integrations.http import ProviderHTTPAdapter
from app.modules.posts.providers import (
    EmbeddingRequest,
    EmbeddingResponse,
    LLMRequest,
    LLMResponse,
    ProviderResponseError,
    VisionRequest,
    VisionResponse,
)


class _OllamaAdapter:
    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
        num_predict: int = 2_048,
        keep_alive: str = "10m",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_predict = num_predict
        self._keep_alive = keep_alive
        self._http = ProviderHTTPAdapter(client=client, timeout_seconds=timeout_seconds)

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._http.post_json(
            provider=self.provider_name,
            url=f"{self._base_url}{endpoint}",
            payload=payload,
        )


#: Ollama's own default. Anything beyond it is dropped without warning.
DEFAULT_CONTEXT_TOKENS = 4_096
#: Ceiling, so an unexpectedly large prompt cannot exhaust the host's memory.
MAX_CONTEXT_TOKENS = 32_768
#: Room for the model's own answer on top of the prompt.
RESPONSE_TOKEN_ALLOWANCE = 2_048
#: A full-canvas plate costs a vision model both the image itself and a long
#: run of reasoning about it that the character estimate cannot see. Measured
#: at 10-14k thinking tokens for a busy 1080px plate on qwen3-vl, so anything
#: smaller returns done_reason=length with an empty answer.
IMAGE_TOKEN_ALLOWANCE = 12_288


def _context_window(estimated_prompt_tokens: int) -> int:
    # Four characters per token is rough, but it only has to be safe: the window
    # is rounded up to a power of two and capped.
    needed = estimated_prompt_tokens + RESPONSE_TOKEN_ALLOWANCE
    window = DEFAULT_CONTEXT_TOKENS
    while window < needed and window < MAX_CONTEXT_TOKENS:
        window *= 2
    return min(window, MAX_CONTEXT_TOKENS)


class OllamaLLMProvider(_OllamaAdapter):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not request.messages:
            raise ValueError("LLM request requires at least one message")
        messages = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive,
            # Hybrid-reasoning models think before answering, and that thinking
            # is discarded: it never reaches the caller and nothing can audit
            # it. Every agent here is required to put its reasoning in the
            # output instead - rationale, basis, weakness - where validation
            # can hold it to something. Models without the capability accept
            # the flag and ignore it.
            "think": False,
            "options": {
                "temperature": request.temperature,
                # Keep structured specialist responses inside the allowance
                # used by context sizing. Without an explicit ceiling Ollama
                # may continue a verbose JSON response until the 300s agent
                # timeout, turning one repair pass into a ten-minute stage.
                "num_predict": self._num_predict,
                # Ollama defaults to a 4k window and silently truncates anything
                # longer, so a large prompt reaches the model with its tail cut
                # off and comes back as malformed output. Size the window to the
                # request instead of hoping the default fits.
                "num_ctx": _context_window(
                    sum(len(message["content"]) for message in messages) // 4
                ),
            },
        }
        if request.response_format == "json":
            payload["format"] = "json"
        body = await self._post("/api/chat", payload)
        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderResponseError("ollama returned an invalid chat response")
        return LLMResponse(
            text=message["content"],
            provider=self.provider_name,
            model=str(body.get("model") or self._model),
            input_tokens=_optional_int(body.get("prompt_eval_count")),
            output_tokens=_optional_int(body.get("eval_count")),
        )


class OllamaVisionProvider(_OllamaAdapter):
    async def analyze(self, request: VisionRequest) -> VisionResponse:
        if not request.image:
            raise ValueError("Vision request image cannot be empty")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": request.prompt,
                    "images": [base64.b64encode(request.image).decode("ascii")],
                }
            ],
            "stream": False,
            "keep_alive": self._keep_alive,
            # Measured on qwen3-vl: this flag alone does not suppress the private
            # reasoning trace, which then costs an order of magnitude more tokens
            # than the answer and exhausts the request timeout. Constrained
            # decoding below is what actually removes it; the flag stays for the
            # vision models that do honour it.
            "think": False,
            "options": {
                "num_predict": self._num_predict,
                # The image and the reasoning a free-form answer still triggers
                # both consume context on top of the prompt itself.
                "num_ctx": _context_window(len(request.prompt) // 4 + IMAGE_TOKEN_ALLOWANCE),
            },
        }
        if request.response_schema is not None:
            payload["format"] = request.response_schema
        body = await self._post("/api/chat", payload)
        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderResponseError("ollama returned an invalid vision response")
        content = message["content"].strip()
        if not content and request.response_schema is not None:
            # A schema-constrained answer carries none of the markers Ollama's
            # renderer splits on, so for some vision models the whole answer is
            # reported as the reasoning trace and `content` arrives empty. The
            # grammar guarantees what is in there is the requested object.
            reasoning = message.get("thinking")
            content = reasoning.strip() if isinstance(reasoning, str) else ""
        if not content:
            # Returning an empty description here would look like a model that
            # saw nothing, which is the opposite of what happened.
            raise ProviderResponseError("ollama returned an empty vision answer")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = {"description": content}
        if not isinstance(parsed, dict):
            parsed = {"result": parsed}
        return VisionResponse(
            data=parsed,
            provider=self.provider_name,
            model=str(body.get("model") or self._model),
        )


class OllamaEmbeddingProvider(_OllamaAdapter):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if not request.texts or any(not text.strip() for text in request.texts):
            raise ValueError("Embedding request requires non-empty texts")
        body = await self._post(
            "/api/embed",
            {"model": self._model, "input": list(request.texts), "keep_alive": self._keep_alive},
        )
        raw_vectors = body.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(request.texts):
            raise ProviderResponseError("ollama returned an invalid embedding response")
        try:
            vectors = tuple(tuple(float(value) for value in vector) for vector in raw_vectors)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError("ollama returned invalid embedding values") from exc
        if not vectors or any(not vector for vector in vectors):
            raise ProviderResponseError("ollama returned empty embeddings")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ProviderResponseError("ollama returned inconsistent embedding dimensions")
        return EmbeddingResponse(
            vectors=vectors,
            provider=self.provider_name,
            model=str(body.get("model") or self._model),
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


__all__ = ["OllamaEmbeddingProvider", "OllamaLLMProvider", "OllamaVisionProvider"]
