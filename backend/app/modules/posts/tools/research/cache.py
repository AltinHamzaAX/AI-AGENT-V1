from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from .schemas import ResearchReport


class ResearchCache(Protocol):
    async def get(self, key: str) -> ResearchReport | None: ...

    async def set(self, key: str, value: ResearchReport, *, ttl_seconds: int) -> None: ...


class InMemoryResearchCache:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._values: dict[str, ResearchReport] = {}

    async def get(self, key: str) -> ResearchReport | None:
        value = self._values.get(key)
        if value is None:
            return None
        if value.expires_at <= self._clock():
            self._values.pop(key, None)
            return None
        return value.model_copy(update={"cached": True})

    async def set(self, key: str, value: ResearchReport, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("research cache TTL must be positive")
        self._values[key] = value.model_copy(deep=True)


__all__ = ["InMemoryResearchCache", "ResearchCache"]
