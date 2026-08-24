"""Future Posts domain exceptions."""


class PostNotFoundError(LookupError):
    pass


class PostSourceNotFoundError(LookupError):
    pass


class PostGenerationNotFoundError(LookupError):
    pass


class WorkflowStateConflictError(RuntimeError):
    pass


class SemanticContractNotFoundError(LookupError):
    pass


class SemanticContractHardFailError(RuntimeError):
    def __init__(self, violations: tuple[str, ...]) -> None:
        super().__init__("Semantic contract violation")
        self.violations = violations


__all__ = [
    "PostGenerationNotFoundError",
    "PostNotFoundError",
    "PostSourceNotFoundError",
    "SemanticContractHardFailError",
    "SemanticContractNotFoundError",
    "WorkflowStateConflictError",
]
