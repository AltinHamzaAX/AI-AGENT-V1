from redis.asyncio import Redis

from app.modules.posts.tools.research import ResearchReport


class RedisResearchCache:
    """Redis adapter for the Posts research-cache contract."""

    def __init__(self, redis: Redis, *, prefix: str = "posts:research:v1") -> None:
        self._redis = redis
        self._prefix = prefix.strip(":")

    async def get(self, key: str) -> ResearchReport | None:
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return ResearchReport.model_validate_json(raw)

    async def set(self, key: str, value: ResearchReport, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("research cache TTL must be positive")
        await self._redis.set(
            self._key(key),
            value.model_dump_json(),
            ex=ttl_seconds,
        )

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"


__all__ = ["RedisResearchCache"]
