import re
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ToolCategory(StrEnum):
    UNDERSTANDING = "understanding"
    ASSETS = "assets"
    RESEARCH = "research"
    MARKETING = "marketing"
    CREATIVE = "creative"
    DESIGN = "design"
    GENERATION = "generation"
    COMPOSITION = "composition"
    VERIFICATION = "verification"
    WORKFLOW = "workflow"


class ToolCapability(StrEnum):
    READ_CONTEXT = "read_context"
    EXTERNAL_IO = "external_io"
    STATE_MUTATION = "state_mutation"
    ASSET_REPLACEMENT = "asset_replacement"
    DATABASE_MUTATION = "database_mutation"
    FINAL_APPROVAL = "final_approval"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0
    retry_on_timeout: bool = False
    retry_on_error: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0 <= self.backoff_seconds <= 60:
            raise ValueError("backoff_seconds must be between 0 and 60")


@dataclass(frozen=True, slots=True)
class ToolSecurityPolicy:
    capabilities: frozenset[ToolCapability] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


#: The longest a specialist agent may run. It is never set below the
#: deployment's provider ceiling: an agent that gives up first would kill a
#: model call the deployment still permits, and report a working stage as a
#: timeout. A slower model is a slower stage, not a broken one.
SPECIALIST_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    role: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_tools: frozenset[str] = frozenset()
    timeout_seconds: float = 60
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        _validate_identifier("agent name", self.name)
        if not self.role.strip():
            raise ValueError("agent role cannot be blank")
        _validate_schema("agent input_schema", self.input_schema)
        _validate_schema("agent output_schema", self.output_schema)
        _validate_timeout("agent timeout_seconds", self.timeout_seconds)
        allowed_tools = frozenset(self.allowed_tools)
        for tool_name in allowed_tools:
            _validate_identifier("allowed tool name", tool_name)
        object.__setattr__(self, "allowed_tools", allowed_tools)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_name: str
    category: ToolCategory
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_agents: frozenset[str] = frozenset()
    timeout_seconds: float = 30
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    security: ToolSecurityPolicy = field(default_factory=ToolSecurityPolicy)

    def __post_init__(self) -> None:
        _validate_identifier("tool_name", self.tool_name)
        _validate_schema("tool input_schema", self.input_schema)
        _validate_schema("tool output_schema", self.output_schema)
        _validate_timeout("tool timeout_seconds", self.timeout_seconds)
        allowed_agents = frozenset(self.allowed_agents)
        for agent_name in allowed_agents:
            _validate_identifier("allowed agent name", agent_name)
        object.__setattr__(self, "allowed_agents", allowed_agents)


@dataclass(frozen=True, slots=True)
class InvocationContext:
    correlation_id: UUID = field(default_factory=uuid4)
    post_id: UUID | None = None
    generation_id: UUID | None = None


def _validate_identifier(name: str, value: str) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase identifier containing 2 to 64 characters")


def _validate_schema(name: str, schema: type[BaseModel]) -> None:
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise TypeError(f"{name} must be a Pydantic BaseModel type")


def _validate_timeout(name: str, value: float) -> None:
    if not 0 < value <= 300:
        raise ValueError(f"{name} must be greater than 0 and at most 300 seconds")


__all__ = [
    "SPECIALIST_TIMEOUT_SECONDS",
    "AgentDefinition",
    "InvocationContext",
    "RetryPolicy",
    "ToolCapability",
    "ToolCategory",
    "ToolDefinition",
    "ToolSecurityPolicy",
]
