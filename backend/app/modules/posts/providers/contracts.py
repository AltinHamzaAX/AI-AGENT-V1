from dataclasses import dataclass, field
from datetime import datetime
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
    topic: str = "general"
    time_range: str | None = None
    country: str | None = None
    include_images: bool = False
    include_raw_content: bool = False


@dataclass(frozen=True, slots=True)
class ResearchResult:
    title: str
    url: str
    content: str
    score: float | None = None
    published_at: datetime | None = None
    #: Extracted page body when requested. `content` is only a search snippet,
    #: which is usually too thin to quote evidence from.
    raw_content: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchImage:
    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchResponse:
    results: tuple[ResearchResult, ...]
    provider: str
    query: str
    answer: str | None = None
    images: tuple[ResearchImage, ...] = ()


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

    async def get(self, *, key: str) -> bytes: ...

    async def delete(self, *, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    llm: LLMProvider
    vision: VisionProvider
    image: ImageProvider
    embedding: EmbeddingProvider
    research: ResearchProvider
    storage: StorageProvider
    #: Set only when a separate model is configured for the stages that invent
    #: rather than extract. Left unset, every stage shares `llm`.
    creative_llm_override: LLMProvider | None = None
    names: dict[str, str] = field(default_factory=dict)

    @property
    def creative_llm(self) -> LLMProvider:
        """The model for work that has to be thought up rather than read off.

        Extraction stages are answerable to evidence in front of them and a
        small model does that well. Invention has nothing to copy from, so the
        deployment can point those stages somewhere stronger without paying
        for it on every other call.
        """
        return self.creative_llm_override or self.llm


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
    "ResearchImage",
    "ResearchProvider",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchResult",
    "StorageProvider",
    "VisionProvider",
    "VisionRequest",
    "VisionResponse",
]
