from typing import Protocol


class ImageGenerationProvider(Protocol):
    async def generate(self, prompt: str) -> bytes: ...
