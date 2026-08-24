from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    temperature: float = 0.2
    response_format: str | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class VisionRequest:
    image: bytes
    mime_type: str
    prompt: str


@dataclass(frozen=True, slots=True)
class VisionResponse:
    data: dict[str, Any]
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class ImageResponse:
    image: bytes
    mime_type: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    texts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    vectors: tuple[tuple[float, ...], ...]
    provider: str
    model: str

    @property
    def dimension(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    query: str
    max_results: int = 5
    search_depth: str = "basic"
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchResult:
    title: str
    url: str
    content: str
    score: float | None = None


@dataclass(frozen=True, slots=True)
class ResearchResponse:
    results: tuple[ResearchResult, ...]
    provider: str
    query: str
    answer: str | None = None


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...


class VisionProvider(Protocol):
    async def analyze(self, request: VisionRequest) -> VisionResponse: ...


class ImageProvider(Protocol):
    async def generate(self, request: ImageRequest) -> ImageResponse: ...


class EmbeddingProvider(Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


class ResearchProvider(Protocol):
    async def search(self, request: ResearchRequest) -> ResearchResponse: ...


class StorageProvider(Protocol):
    async def is_available(self) -> bool: ...

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    async def delete(self, *, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    llm: LLMProvider
    vision: VisionProvider
    image: ImageProvider
    embedding: EmbeddingProvider
    research: ResearchProvider
    storage: StorageProvider
    names: dict[str, str] = field(default_factory=dict)


__all__ = [
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "ImageProvider",
    "ImageRequest",
    "ImageResponse",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ProviderBundle",
    "ResearchProvider",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchResult",
    "StorageProvider",
    "VisionProvider",
    "VisionRequest",
    "VisionResponse",
]
