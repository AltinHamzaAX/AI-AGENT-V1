from typing import Protocol


class VisionProvider(Protocol):
    async def analyze(self, image: bytes) -> dict[str, object]: ...
