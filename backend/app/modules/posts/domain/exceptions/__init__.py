"""Future Posts domain exceptions."""


class PostNotFoundError(LookupError):
    pass


class PostSourceNotFoundError(LookupError):
    pass


class PostGenerationNotFoundError(LookupError):
    pass


class WorkflowStateConflictError(RuntimeError):
    pass


__all__ = [
    "PostGenerationNotFoundError",
    "PostNotFoundError",
    "PostSourceNotFoundError",
    "WorkflowStateConflictError",
]
