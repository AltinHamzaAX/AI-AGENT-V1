from typing import Protocol


class PostGenerationService(Protocol):
    """Future public entry point for post generation requests."""

    async def request_generation(self, brief: object) -> str: ...
