import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from app.infrastructure.cache.redis import get_redis
from app.infrastructure.database.session import engine
from app.infrastructure.storage.s3 import S3Storage

logger = logging.getLogger(__name__)


async def _database_check() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _pgvector_check() -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        if result.scalar_one_or_none() is None:
            raise RuntimeError("pgvector extension is not enabled")


async def _redis_check() -> None:
    if not await get_redis().ping():
        raise ConnectionError("Redis ping failed")


async def _storage_check() -> None:
    await S3Storage().is_available()


async def _result(name: str, check: Callable[[], Awaitable[None]]) -> tuple[str, str]:
    try:
        await asyncio.wait_for(check(), timeout=5)
        return name, "ok"
    except Exception as exc:
        logger.warning("Readiness check failed for %s: %s", name, type(exc).__name__)
        return name, "unavailable"


async def check_dependencies() -> dict[str, str]:
    results = await asyncio.gather(
        _result("database", _database_check),
        _result("pgvector", _pgvector_check),
        _result("redis", _redis_check),
        _result("storage", _storage_check),
    )
    return dict(results)
