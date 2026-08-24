import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any, TypeVar

from app.modules.posts.domain.contracts import InvocationContext
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    ExecutionTraceCreate,
    ExecutionTraceRecorder,
    safe_error_code,
    trace_reference,
)
from app.modules.posts.providers import (
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
    ImageProvider,
    ImageRequest,
    ImageResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderBundle,
    ResearchProvider,
    ResearchRequest,
    ResearchResponse,
    StorageProvider,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TracedLLMProvider:
    def __init__(self, provider: LLMProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._trace.call(
            "llm.complete", request, lambda: self._provider.complete(request)
        )


class TracedVisionProvider:
    def __init__(self, provider: VisionProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        return await self._trace.call(
            "vision.analyze", request, lambda: self._provider.analyze(request)
        )


class TracedImageProvider:
    def __init__(self, provider: ImageProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def generate(self, request: ImageRequest) -> ImageResponse:
        return await self._trace.call(
            "image.generate", request, lambda: self._provider.generate(request)
        )


class TracedEmbeddingProvider:
    def __init__(self, provider: EmbeddingProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return await self._trace.call(
            "embedding.embed", request, lambda: self._provider.embed(request)
        )


class TracedResearchProvider:
    def __init__(self, provider: ResearchProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def search(self, request: ResearchRequest) -> ResearchResponse:
        return await self._trace.call(
            "research.search", request, lambda: self._provider.search(request)
        )


class TracedStorageProvider:
    def __init__(self, provider: StorageProvider, trace: "_ProviderTrace") -> None:
        self._provider = provider
        self._trace = trace

    async def is_available(self) -> bool:
        return await self._trace.call(
            "storage.is_available", {}, self._provider.is_available
        )

    async def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        await self._trace.call(
            "storage.put",
            {"key": key, "data": data, "content_type": content_type, "metadata": metadata},
            lambda: self._provider.put(
                key=key,
                data=data,
                content_type=content_type,
                metadata=metadata,
            ),
        )

    async def delete(self, *, key: str) -> None:
        await self._trace.call(
            "storage.delete", {"key": key}, lambda: self._provider.delete(key=key)
        )


class _ProviderTrace:
    def __init__(
        self,
        *,
        recorder: ExecutionTraceRecorder,
        invocation: InvocationContext,
        provider_names: dict[str, str],
    ) -> None:
        if invocation.generation_id is None:
            raise ValueError("provider tracing requires invocation.generation_id")
        self._recorder = recorder
        self._invocation = invocation
        self._provider_names = dict(provider_names)

    async def call(
        self,
        name: str,
        request: Any,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        capability = name.split(".", 1)[0]
        started_at = monotonic()
        started_wall = datetime.now(UTC)
        input_reference = trace_reference(request)
        try:
            response = await operation()
        except Exception as exc:
            await self._record(
                name=name,
                status=(
                    ExecutionRunStatus.TIMEOUT
                    if isinstance(exc, TimeoutError)
                    else ExecutionRunStatus.FAILED
                ),
                started_at=started_wall,
                duration_ms=_duration_ms(started_at),
                input_reference=input_reference,
                provider=self._provider_names.get(capability),
                error_code=safe_error_code(exc),
            )
            raise
        await self._record(
            name=name,
            status=ExecutionRunStatus.SUCCEEDED,
            started_at=started_wall,
            duration_ms=_duration_ms(started_at),
            input_reference=input_reference,
            output_reference=trace_reference(response),
            provider=getattr(response, "provider", None) or self._provider_names.get(capability),
            model=getattr(response, "model", None),
            input_tokens=getattr(response, "input_tokens", None),
            output_tokens=getattr(response, "output_tokens", None),
        )
        return response

    async def _record(self, *, name: str, **fields: Any) -> None:
        try:
            await self._recorder.record(
                ExecutionTraceCreate(
                    generation_id=self._invocation.generation_id,  # type: ignore[arg-type]
                    correlation_id=self._invocation.correlation_id,
                    kind=ExecutionRunKind.PROVIDER,
                    name=name,
                    **fields,
                )
            )
        except Exception:  # noqa: BLE001 - telemetry must not mask provider behavior
            logger.exception("posts.trace.record_failed", extra={"trace_kind": "provider"})


def trace_provider_bundle(
    bundle: ProviderBundle,
    *,
    recorder: ExecutionTraceRecorder,
    invocation: InvocationContext,
) -> ProviderBundle:
    trace = _ProviderTrace(
        recorder=recorder,
        invocation=invocation,
        provider_names=bundle.names,
    )
    return ProviderBundle(
        llm=TracedLLMProvider(bundle.llm, trace),
        vision=TracedVisionProvider(bundle.vision, trace),
        image=TracedImageProvider(bundle.image, trace),
        embedding=TracedEmbeddingProvider(bundle.embedding, trace),
        research=TracedResearchProvider(bundle.research, trace),
        storage=TracedStorageProvider(bundle.storage, trace),
        names=dict(bundle.names),
    )


def _duration_ms(started_at: float) -> int:
    return max(0, round((monotonic() - started_at) * 1000))


__all__ = ["trace_provider_bundle"]
