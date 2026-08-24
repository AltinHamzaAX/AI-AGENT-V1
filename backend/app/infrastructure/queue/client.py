from typing import Protocol


class JobQueue(Protocol):
    async def enqueue(self, job_name: str, payload: dict[str, object]) -> str: ...
