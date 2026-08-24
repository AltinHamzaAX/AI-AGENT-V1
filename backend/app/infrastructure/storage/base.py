from typing import Protocol


class ObjectStorage(Protocol):
    async def is_available(self) -> bool: ...
