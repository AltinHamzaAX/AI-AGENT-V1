from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.repositories.base import SQLAlchemyRepository
from app.infrastructure.database.unit_of_work import SQLAlchemyUnitOfWork


class PersistenceTestBase(DeclarativeBase):
    pass


class PersistenceProbe(PersistenceTestBase):
    __tablename__ = "persistence_probes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


@pytest_asyncio.fixture
async def persistence_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(PersistenceTestBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_unit_of_work_commits_repository_crud(
    persistence_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(persistence_session_factory)
    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.session

    async with unit_of_work:
        repository = SQLAlchemyRepository[PersistenceProbe, int](
            unit_of_work.session,
            PersistenceProbe,
        )
        alpha = await repository.add(PersistenceProbe(name="alpha"))
        beta = await repository.add(PersistenceProbe(name="beta"))
        gamma = await repository.add(PersistenceProbe(name="gamma"))
        await unit_of_work.commit()

    with pytest.raises(RuntimeError, match="not active"):
        _ = unit_of_work.session

    async with SQLAlchemyUnitOfWork(persistence_session_factory) as read_uow:
        repository = SQLAlchemyRepository[PersistenceProbe, int](
            read_uow.session,
            PersistenceProbe,
        )
        found = await repository.get(alpha.id)
        assert found is not None
        assert found.name == "alpha"
        assert [probe.name for probe in await repository.list(offset=1, limit=2)] == [
            "beta",
            "gamma",
        ]

        beta.name = "beta-updated"
        updated = await repository.update(beta)
        assert updated.name == "beta-updated"
        assert await repository.delete(gamma.id) is True
        assert await repository.delete(999_999) is False
        await read_uow.commit()

    async with persistence_session_factory() as session:
        names = list(
            (
                await session.execute(select(PersistenceProbe.name).order_by(PersistenceProbe.id))
            ).scalars()
        )
    assert names == ["alpha", "beta-updated"]


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_without_explicit_commit(
    persistence_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SQLAlchemyUnitOfWork(persistence_session_factory) as unit_of_work:
        repository = SQLAlchemyRepository[PersistenceProbe, int](
            unit_of_work.session,
            PersistenceProbe,
        )
        await repository.add(PersistenceProbe(name="must-not-persist"))

    assert await _probe_count(persistence_session_factory) == 0


@pytest.mark.asyncio
async def test_unit_of_work_explicit_rollback_discards_changes(
    persistence_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SQLAlchemyUnitOfWork(persistence_session_factory) as unit_of_work:
        repository = SQLAlchemyRepository[PersistenceProbe, int](
            unit_of_work.session,
            PersistenceProbe,
        )
        await repository.add(PersistenceProbe(name="rolled-back"))
        await unit_of_work.rollback()

    assert await _probe_count(persistence_session_factory) == 0


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_when_exception_escapes(
    persistence_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        async with SQLAlchemyUnitOfWork(persistence_session_factory) as unit_of_work:
            repository = SQLAlchemyRepository[PersistenceProbe, int](
                unit_of_work.session,
                PersistenceProbe,
            )
            await repository.add(PersistenceProbe(name="exception"))
            raise RuntimeError("force rollback")

    assert await _probe_count(persistence_session_factory) == 0


@pytest.mark.asyncio
async def test_unit_of_work_rejects_nested_entry(
    persistence_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unit_of_work = SQLAlchemyUnitOfWork(persistence_session_factory)
    async with unit_of_work:
        with pytest.raises(RuntimeError, match="already active"):
            await unit_of_work.__aenter__()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offset", "limit", "message"),
    [
        (-1, 10, "offset must be non-negative"),
        (0, 0, "limit must be between"),
        (0, 1_001, "limit must be between"),
    ],
)
async def test_repository_rejects_invalid_pagination(
    persistence_session_factory: async_sessionmaker[AsyncSession],
    offset: int,
    limit: int,
    message: str,
) -> None:
    async with SQLAlchemyUnitOfWork(persistence_session_factory) as unit_of_work:
        repository = SQLAlchemyRepository[PersistenceProbe, int](
            unit_of_work.session,
            PersistenceProbe,
        )
        with pytest.raises(ValueError, match=message):
            await repository.list(offset=offset, limit=limit)


def test_agents_and_services_do_not_import_database_implementations() -> None:
    app_root = Path(__file__).parents[1] / "app"
    candidates = [
        *app_root.glob("modules/*/agents/**/*.py"),
        *app_root.glob("modules/*/services/**/*.py"),
        *app_root.glob("shared/*/service.py"),
    ]
    prohibited = (
        "import sqlalchemy",
        "from sqlalchemy",
        "import asyncpg",
        "from asyncpg",
        "app.infrastructure.database",
        "app.models",
    )

    violations = []
    for path in candidates:
        source = path.read_text(encoding="utf-8")
        for marker in prohibited:
            if marker in source:
                violations.append(f"{path.relative_to(app_root)} imports {marker}")

    assert violations == []


async def _probe_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        return int((await session.execute(select(func.count(PersistenceProbe.id)))).scalar_one())
