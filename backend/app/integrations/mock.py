import base64
import hashlib

from app.modules.posts.providers import (
    EmbeddingRequest,
    EmbeddingResponse,
    ImageRequest,
    ImageResponse,
    LLMRequest,
    LLMResponse,
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
    VisionRequest,
    VisionResponse,
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class MockLLMProvider:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = request.messages[-1].content if request.messages else ""
        return LLMResponse(text=text, provider="mock", model="mock-llm")


class MockVisionProvider:
    async def analyze(self, request: VisionRequest) -> VisionResponse:
        return VisionResponse(
            data={"description": request.prompt, "size_bytes": len(request.image)},
            provider="mock",
            model="mock-vision",
        )


class MockImageProvider:
    async def generate(self, request: ImageRequest) -> ImageResponse:
        if not request.prompt.strip():
            raise ValueError("Image prompt cannot be empty")
        return ImageResponse(
            image=_ONE_PIXEL_PNG,
            mime_type="image/png",
            provider="mock",
            model="mock-image",
        )


class MockEmbeddingProvider:
    def __init__(self, *, dimension: int = 8) -> None:
        self._dimension = dimension

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        vectors = tuple(self._vector(text) for text in request.texts)
        return EmbeddingResponse(
            vectors=vectors,
            provider="mock",
            model="mock-embedding",
        )

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple((digest[index] / 127.5) - 1.0 for index in range(self._dimension))


class MockResearchProvider:
    async def search(self, request: ResearchRequest) -> ResearchResponse:
        result = ResearchResult(
            title=f"Mock result for {request.query}",
            url="https://example.test/research",
            content="Deterministic mock research content.",
            score=1.0,
        )
        return ResearchResponse(
            results=(result,) if request.max_results else (),
            provider="mock",
            query=request.query,
            answer="Deterministic mock answer.",
        )


class MockStorageProvider:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}

    async def is_available(self) -> bool:
        return True

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.objects[key] = (data, content_type, dict(metadata or {}))

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


__all__ = [
    "MockEmbeddingProvider",
    "MockImageProvider",
    "MockLLMProvider",
    "MockResearchProvider",
    "MockStorageProvider",
    "MockVisionProvider",
]
