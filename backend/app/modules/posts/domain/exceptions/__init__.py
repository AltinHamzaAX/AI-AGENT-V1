"""Future Posts domain exceptions."""

from app.modules.posts.domain.exceptions.agent_tools import (
    AgentNotFoundError,
    AgentToolFrameworkError,
    DuplicateRegistrationError,
    InvocationFailedError,
    InvocationTimeoutError,
    ToolNotFoundError,
    UnauthorizedToolInvocationError,
)


class PostNotFoundError(LookupError):
    pass


class PostSourceNotFoundError(LookupError):
    pass


class PostGenerationNotFoundError(LookupError):
    pass


class ChatMessageNotFoundError(LookupError):
    """The turn targets a message that is not this conversation's open client turn."""


class WorkflowStateConflictError(RuntimeError):
    pass


class SemanticContractNotFoundError(LookupError):
    pass


class SemanticContractHardFailError(RuntimeError):
    def __init__(self, violations: tuple[str, ...]) -> None:
        super().__init__("Semantic contract violation")
        self.violations = violations


class BenchmarkCaseNotFoundError(LookupError):
    pass


class BenchmarkReviewConflictError(RuntimeError):
    pass


class BenchmarkGenerationNotReadyError(RuntimeError):
    pass


__all__ = [
    "AgentNotFoundError",
    "AgentToolFrameworkError",
    "BenchmarkCaseNotFoundError",
    "BenchmarkGenerationNotReadyError",
    "BenchmarkReviewConflictError",
    "ChatMessageNotFoundError",
    "DuplicateRegistrationError",
    "InvocationFailedError",
    "InvocationTimeoutError",
    "PostGenerationNotFoundError",
    "PostNotFoundError",
    "PostSourceNotFoundError",
    "SemanticContractHardFailError",
    "SemanticContractNotFoundError",
    "ToolNotFoundError",
    "UnauthorizedToolInvocationError",
    "WorkflowStateConflictError",
]
