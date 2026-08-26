from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.integrations.mock import (
    MockEmbeddingProvider,
    MockImageProvider,
    MockLLMProvider,
    MockResearchProvider,
    MockStorageProvider,
    MockVisionProvider,
)
from app.integrations.tracing import trace_provider_bundle
from app.modules.posts.agents import AgentRuntime
from app.modules.posts.domain.contracts import (
    AgentDefinition,
    InvocationContext,
    ToolCategory,
    ToolDefinition,
)
from app.modules.posts.domain.observability import (
    ExecutionRunKind,
    ExecutionRunStatus,
    InMemoryExecutionTraceRecorder,
)
from app.modules.posts.providers import (
    EmbeddingRequest,
    ImageRequest,
    LLMMessage,
    LLMRequest,
    ProviderBundle,
    ResearchRequest,
    VisionRequest,
)
from app.modules.posts.tools import ToolRegistry


class _Input(BaseModel):
    text: str


class _Output(BaseModel):
    result: str


@pytest.mark.asyncio
async def test_agent_and_tool_runs_are_traced_without_raw_payloads() -> None:
    recorder = InMemoryExecutionTraceRecorder()
    tools = ToolRegistry(trace_recorder=recorder)
    tools.register(
        ToolDefinition(
            tool_name="uppercase_text",
            category=ToolCategory.UNDERSTANDING,
            input_schema=_Input,
            output_schema=_Output,
            allowed_agents=frozenset({"client_understanding"}),
        ),
        lambda payload, _: _async_result({"result": payload.text.upper()}),
    )
    runtime = AgentRuntime(tools, trace_recorder=recorder)

    async def handler(payload: BaseModel, gateway, _context):
        return await gateway.invoke("uppercase_text", payload)

    runtime.register(
        AgentDefinition(
            name="client_understanding",
            role="Understand a client brief",
            input_schema=_Input,
            output_schema=_Output,
            allowed_tools=frozenset({"uppercase_text"}),
        ),
        handler,
    )
    invocation = InvocationContext(post_id=uuid4(), generation_id=uuid4())
    secret = "private-token-value"

    result = await runtime.run(
        "client_understanding",
        {"text": secret},
        invocation=invocation,
    )

    assert result.result == secret.upper()
    assert [trace.kind for trace in recorder.traces] == [
        ExecutionRunKind.TOOL,
        ExecutionRunKind.AGENT,
    ]
    assert all(trace.status is ExecutionRunStatus.SUCCEEDED for trace in recorder.traces)
    assert all(trace.input_reference.startswith("sha256:") for trace in recorder.traces)
    assert secret not in repr(recorder.traces)


@pytest.mark.asyncio
async def test_provider_calls_capture_provider_model_and_token_metadata() -> None:
    recorder = InMemoryExecutionTraceRecorder()
    invocation = InvocationContext(post_id=uuid4(), generation_id=uuid4())
    bundle = ProviderBundle(
        llm=MockLLMProvider(),
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={
            "llm": "mock",
            "vision": "mock",
            "image": "mock",
            "embedding": "mock",
            "research": "mock",
            "storage": "mock",
        },
    )
    traced = trace_provider_bundle(bundle, recorder=recorder, invocation=invocation)
    secret = "confidential campaign brief"

    response = await traced.llm.complete(
        LLMRequest(messages=(LLMMessage(role="user", content=secret),))
    )
    await traced.vision.analyze(
        VisionRequest(image=b"private-image", mime_type="image/png", prompt="describe")
    )
    await traced.image.generate(ImageRequest(prompt="clean studio background"))
    await traced.embedding.embed(EmbeddingRequest(texts=("audience insight",)))
    await traced.research.search(ResearchRequest(query="Kosovo rental market"))
    await traced.storage.put(
        key="trace/test.txt",
        data=b"private-storage-data",
        content_type="text/plain",
    )

    assert response.text == secret
    trace = recorder.traces[0]
    assert trace.kind is ExecutionRunKind.PROVIDER
    assert trace.name == "llm.complete"
    assert trace.provider == "mock"
    assert trace.model == "mock-llm"
    assert trace.input_reference.startswith("sha256:")
    assert secret not in repr(trace)
    assert [item.name for item in recorder.traces] == [
        "llm.complete",
        "vision.analyze",
        "image.generate",
        "embedding.embed",
        "research.search",
        "storage.put",
    ]
    assert all(item.kind is ExecutionRunKind.PROVIDER for item in recorder.traces)
    assert "private-image" not in repr(recorder.traces)
    assert "private-storage-data" not in repr(recorder.traces)



@pytest.mark.asyncio
async def test_a_separate_creative_model_is_traced_like_any_other_provider() -> None:
    """An untraced provider is an unobservable one, whichever stage holds it."""
    recorder = InMemoryExecutionTraceRecorder()
    invocation = InvocationContext(post_id=uuid4(), generation_id=uuid4())
    bundle = ProviderBundle(
        llm=MockLLMProvider(),
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        creative_llm_override=MockLLMProvider(),
        names={"llm": "mock"},
    )

    traced = trace_provider_bundle(bundle, recorder=recorder, invocation=invocation)
    await traced.creative_llm.complete(
        LLMRequest(messages=(LLMMessage(role="user", content="invent"),))
    )

    assert traced.creative_llm is not traced.llm
    assert [trace.name for trace in recorder.traces] == ["llm.complete"]

@pytest.mark.asyncio
async def test_validation_and_provider_errors_record_only_safe_error_codes() -> None:
    recorder = InMemoryExecutionTraceRecorder()
    tools = ToolRegistry(trace_recorder=recorder)
    runtime = AgentRuntime(tools, trace_recorder=recorder)
    runtime.register(
        AgentDefinition(
            name="client_understanding",
            role="Understand a client brief",
            input_schema=_Input,
            output_schema=_Output,
        ),
        lambda *_: _async_result({"result": "unused"}),
    )
    invocation = InvocationContext(post_id=uuid4(), generation_id=uuid4())

    with pytest.raises(ValueError):
        await runtime.run("client_understanding", {}, invocation=invocation)

    validation_trace = recorder.traces[-1]
    assert validation_trace.status is ExecutionRunStatus.FAILED
    assert validation_trace.error_code == "ValidationError"
    assert validation_trace.duration_ms == 0

    bundle = ProviderBundle(
        llm=_FailingLLMProvider(),
        vision=MockVisionProvider(),
        image=MockImageProvider(),
        embedding=MockEmbeddingProvider(),
        research=MockResearchProvider(),
        storage=MockStorageProvider(),
        names={"llm": "safe-provider"},
    )
    traced = trace_provider_bundle(bundle, recorder=recorder, invocation=invocation)
    with pytest.raises(RuntimeError, match="credential-secret"):
        await traced.llm.complete(
            LLMRequest(messages=(LLMMessage(role="user", content="private prompt"),))
        )

    failure_trace = recorder.traces[-1]
    assert failure_trace.error_code == "RuntimeError"
    assert failure_trace.provider == "safe-provider"
    assert "credential-secret" not in repr(failure_trace)
    assert "private prompt" not in repr(failure_trace)


class _FailingLLMProvider:
    async def complete(self, _request: LLMRequest):
        raise RuntimeError("credential-secret must never be persisted")


async def _async_result(value):
    return value
