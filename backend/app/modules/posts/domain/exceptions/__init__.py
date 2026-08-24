"""Future Posts domain exceptions."""


class PostNotFoundError(LookupError):
    pass


class PostSourceNotFoundError(LookupError):
    pass


class PostGenerationNotFoundError(LookupError):
    pass


__all__ = ["PostGenerationNotFoundError", "PostNotFoundError", "PostSourceNotFoundError"]
