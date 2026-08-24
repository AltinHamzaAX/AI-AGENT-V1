class AgentToolFrameworkError(RuntimeError):
    pass


class DuplicateRegistrationError(AgentToolFrameworkError):
    pass


class AgentNotFoundError(AgentToolFrameworkError):
    pass


class ToolNotFoundError(AgentToolFrameworkError):
    pass


class UnauthorizedToolInvocationError(AgentToolFrameworkError):
    def __init__(self, *, agent_name: str, tool_name: str, reason: str) -> None:
        super().__init__(
            f"Agent '{agent_name}' is not authorized to invoke tool '{tool_name}': {reason}"
        )
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.reason = reason


class InvocationTimeoutError(AgentToolFrameworkError):
    def __init__(self, *, component: str, name: str, attempts: int) -> None:
        super().__init__(f"{component} '{name}' timed out after {attempts} attempt(s)")
        self.component = component
        self.name = name
        self.attempts = attempts


class InvocationFailedError(AgentToolFrameworkError):
    def __init__(self, *, component: str, name: str, attempts: int) -> None:
        super().__init__(f"{component} '{name}' failed after {attempts} attempt(s)")
        self.component = component
        self.name = name
        self.attempts = attempts


__all__ = [
    "AgentNotFoundError",
    "AgentToolFrameworkError",
    "DuplicateRegistrationError",
    "InvocationFailedError",
    "InvocationTimeoutError",
    "ToolNotFoundError",
    "UnauthorizedToolInvocationError",
]
