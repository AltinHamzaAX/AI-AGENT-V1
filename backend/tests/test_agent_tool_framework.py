import asyncio
import logging
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.modules.posts.agents import AgentRuntime
from app.modules.posts.domain.contracts import (
    AgentDefinition,
    InvocationContext,
    RetryPolicy,
    ToolCapability,
    ToolCategory,
    ToolDefinition,
    ToolSecurityPolicy,
)
from app.modules.posts.domain.exceptions import (
    AgentNotFoundError,
    DuplicateRegistrationError,
    InvocationFailedError,
    InvocationTimeoutError,
    ToolNotFoundError,
    UnauthorizedToolInvocationError,
)
from app.modules.posts.tools import ToolRegistry


class TextInput(BaseModel):
    text: str = Field(min_length=1)


class TextOutput(BaseModel):
    result: str


def _agent_definition(
    *,
    name: str = "copywriter",
    allowed_tools: frozenset[str] = frozenset({"uppercase_text"}),
    timeout_seconds: float = 1,
    retry_policy: RetryPolicy | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        role="Produce a validated test result",
        input_schema=TextInput,
        output_schema=TextOutput,
        allowed_tools=allowed_tools,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
    )


def _tool_definition(
    *,
    tool_name: str = "uppercase_text",
    allowed_agents: frozenset[str] = frozenset({"copywriter"}),
    capability: ToolCapability = ToolCapability.READ_CONTEXT,
    timeout_seconds: float = 1,
    retry_policy: RetryPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_name=tool_name,
        category=ToolCategory.UNDERSTANDING,
        input_schema=TextInput,
        output_schema=TextOutput,
        allowed_agents=allowed_agents,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
        security=ToolSecurityPolicy(capabilities=frozenset({capability})),
    )


@pytest.mark.asyncio
async def test_agent_runtime_validates_schemas_and_invokes_authorized_tool(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tools = ToolRegistry()

    async def uppercase(payload: BaseModel, context) -> dict[str, str]:
        assert context.agent_name == "copywriter"
        assert context.tool_name == "uppercase_text"
        assert context.attempt == 1
        assert isinstance(payload, TextInput)
        return {"result": payload.text.upper()}

    tools.register(_tool_definition(), uppercase)
    runtime = AgentRuntime(tools)

    async def run_copywriter(payload: BaseModel, gateway, context) -> dict[str, str]:
        assert context.agent_name == "copywriter"
        tool_output = await gateway.invoke("uppercase_text", payload)
        assert isinstance(tool_output, TextOutput)
        return {"result": tool_output.result}

    runtime.register(_agent_definition(), run_copywriter)
    invocation = InvocationContext(
        post_id=uuid4(),
        generation_id=uuid4(),
    )
    secret_input = "private brief 123"
    with caplog.at_level(logging.INFO):
        output = await runtime.run(
            "copywriter",
            {"text": secret_input},
            invocation=invocation,
        )

    assert output == TextOutput(result=secret_input.upper())
    assert [definition.name for definition in runtime.definitions()] == ["copywriter"]
    assert [definition.tool_name for definition in tools.definitions()] == ["uppercase_text"]
    assert "posts.agent.succeeded" in caplog.messages
    assert "posts.tool.succeeded" in caplog.messages
    assert secret_input not in caplog.text
    success_record = next(
        record for record in caplog.records if record.getMessage() == "posts.tool.succeeded"
    )
    assert success_record.correlation_id == str(invocation.correlation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("denial_side", ["agent", "tool"])
async def test_unauthorized_tool_invocation_is_denied_and_audited(
    caplog: pytest.LogCaptureFixture,
    denial_side: str,
) -> None:
    tools = ToolRegistry()
    called = False

    async def prohibited_handler(payload, context):
        nonlocal called
        called = True
        return {"result": "must not run"}

    tool_agents = frozenset({"copywriter"}) if denial_side == "agent" else frozenset()
    agent_tools = frozenset() if denial_side == "agent" else frozenset({"uppercase_text"})
    tools.register(_tool_definition(allowed_agents=tool_agents), prohibited_handler)
    runtime = AgentRuntime(tools)

    async def agent_handler(payload, gateway, context):
        return await gateway.invoke("uppercase_text", payload)

    runtime.register(
        _agent_definition(
            allowed_tools=agent_tools,
            retry_policy=RetryPolicy(max_attempts=3, retry_on_error=True),
        ),
        agent_handler,
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(UnauthorizedToolInvocationError) as error:
            await runtime.run("copywriter", {"text": "secret payload"})

    assert called is False
    assert error.value.reason in {
        "tool is not in agent.allowed_tools",
        "agent is not in tool.allowed_agents",
    }
    denied = [record for record in caplog.records if record.event == "posts.tool.denied"]
    assert len(denied) == 1
    assert denied[0].agent_name == "copywriter"
    assert denied[0].tool_name == "uppercase_text"
    assert "secret payload" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        ToolCapability.FINAL_APPROVAL,
        ToolCapability.DATABASE_MUTATION,
        ToolCapability.ASSET_REPLACEMENT,
    ],
)
async def test_creative_director_can_never_invoke_privileged_capability(
    capability: ToolCapability,
) -> None:
    tools = ToolRegistry()
    called = False

    async def privileged_handler(payload, context):
        nonlocal called
        called = True
        return {"result": "forbidden"}

    tools.register(
        _tool_definition(
            tool_name="privileged_action",
            allowed_agents=frozenset({"creative_director"}),
            capability=capability,
        ),
        privileged_handler,
    )
    runtime = AgentRuntime(tools)

    async def creative_director(payload, gateway, context):
        return await gateway.invoke("privileged_action", payload)

    runtime.register(
        _agent_definition(
            name="creative_director",
            allowed_tools=frozenset({"privileged_action"}),
        ),
        creative_director,
    )

    with pytest.raises(UnauthorizedToolInvocationError, match=capability.value):
        await runtime.run("creative_director", {"text": "concept"})
    assert called is False


@pytest.mark.asyncio
async def test_tool_retry_policy_retries_error_then_validates_output() -> None:
    tools = ToolRegistry()
    attempts = 0

    async def flaky_tool(payload: TextInput, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient provider error")
        return {"result": payload.text.upper()}

    tools.register(
        _tool_definition(
            retry_policy=RetryPolicy(max_attempts=2, retry_on_error=True),
        ),
        flaky_tool,
    )
    runtime = AgentRuntime(tools)

    async def agent_handler(payload, gateway, context):
        return await gateway.invoke("uppercase_text", payload)

    runtime.register(_agent_definition(), agent_handler)
    output = await runtime.run("copywriter", {"text": "retry"})
    assert output == TextOutput(result="RETRY")
    assert attempts == 2


@pytest.mark.asyncio
async def test_tool_timeout_honors_retry_limit() -> None:
    tools = ToolRegistry()
    attempts = 0

    async def slow_tool(payload, context):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)
        return {"result": "late"}

    tools.register(
        _tool_definition(
            timeout_seconds=0.001,
            retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True),
        ),
        slow_tool,
    )
    runtime = AgentRuntime(tools)

    async def agent_handler(payload, gateway, context):
        return await gateway.invoke("uppercase_text", payload)

    runtime.register(_agent_definition(), agent_handler)
    with pytest.raises(InvocationTimeoutError) as error:
        await runtime.run("copywriter", {"text": "timeout"})
    assert error.value.component == "tool"
    assert error.value.attempts == 2
    assert attempts == 2


@pytest.mark.asyncio
async def test_agent_retry_timeout_and_output_validation_are_enforced() -> None:
    tools = ToolRegistry()
    runtime = AgentRuntime(tools)
    attempts = 0

    async def invalid_then_valid(payload, gateway, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"wrong": "shape"}
        return {"result": payload.text}

    runtime.register(
        _agent_definition(
            allowed_tools=frozenset(),
            retry_policy=RetryPolicy(max_attempts=2, retry_on_error=True),
        ),
        invalid_then_valid,
    )
    assert await runtime.run("copywriter", {"text": "valid"}) == TextOutput(result="valid")
    assert attempts == 2

    timeout_runtime = AgentRuntime(tools)
    timeout_attempts = 0

    async def slow_agent(payload, gateway, context):
        nonlocal timeout_attempts
        timeout_attempts += 1
        await asyncio.sleep(0.05)
        return {"result": "late"}

    timeout_runtime.register(
        _agent_definition(
            name="slow_agent",
            allowed_tools=frozenset(),
            timeout_seconds=0.001,
            retry_policy=RetryPolicy(max_attempts=2, retry_on_timeout=True),
        ),
        slow_agent,
    )
    with pytest.raises(InvocationTimeoutError) as error:
        await timeout_runtime.run("slow_agent", {"text": "timeout"})
    assert error.value.component == "agent"
    assert error.value.attempts == 2
    assert timeout_attempts == 2


@pytest.mark.asyncio
async def test_registration_lookup_and_validation_fail_closed() -> None:
    tools = ToolRegistry()

    async def handler(payload, context):
        return {"result": "ok"}

    definition = _tool_definition()
    tools.register(definition, handler)
    with pytest.raises(DuplicateRegistrationError):
        tools.register(definition, handler)
    with pytest.raises(ToolNotFoundError):
        tools.get_definition("missing_tool")

    runtime = AgentRuntime(tools)

    async def agent_handler(payload, gateway, context):
        return {"result": "ok"}

    agent = _agent_definition(allowed_tools=frozenset())
    runtime.register(agent, agent_handler)
    with pytest.raises(DuplicateRegistrationError):
        runtime.register(agent, agent_handler)
    with pytest.raises(AgentNotFoundError):
        await runtime.run("missing_agent", {"text": "x"})
    with pytest.raises(ValidationError):
        await runtime.run("copywriter", {"text": ""})

    with pytest.raises(ValueError):
        _agent_definition(name="Invalid Name")
    with pytest.raises(ValueError):
        _tool_definition(timeout_seconds=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


@pytest.mark.asyncio
async def test_invalid_tool_output_fails_without_leaking_handler_error() -> None:
    tools = ToolRegistry()

    async def invalid_output(payload, context):
        return {"unexpected": "value"}

    tools.register(_tool_definition(), invalid_output)
    runtime = AgentRuntime(tools)

    async def agent_handler(payload, gateway, context):
        return await gateway.invoke("uppercase_text", payload)

    runtime.register(_agent_definition(), agent_handler)
    with pytest.raises(InvocationFailedError) as error:
        await runtime.run("copywriter", {"text": "safe"})
    assert error.value.component == "tool"
    assert "unexpected" not in str(error.value)
