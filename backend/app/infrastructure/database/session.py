from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


settings = get_settings()
engine = build_engine(settings.database_url)
AsyncSessionFactory = build_session_factory(engine)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


async def get_db_transaction() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory.begin() as session:
        yield session


async def close_database() -> None:
    await engine.dispose()
