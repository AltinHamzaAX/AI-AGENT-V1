from typing import Protocol


class ResearchProvider(Protocol):
    async def search(self, query: str) -> list[dict[str, object]]: ...
