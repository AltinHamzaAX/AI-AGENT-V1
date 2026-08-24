from collections.abc import Sequence

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.base import Base

MAX_PAGE_SIZE = 1_000


class SQLAlchemyRepository[ModelT: Base, IdentifierT]:
    """Reusable SQLAlchemy CRUD adapter; transaction ownership stays with a UoW."""

    def __init__(self, session: AsyncSession, model_type: type[ModelT]) -> None:
        self._session = session
        self._model_type = model_type

    async def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get(self, identifier: IdentifierT) -> ModelT | None:
        return await self._session.get(self._model_type, identifier)

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[ModelT]:
        self._validate_page(offset=offset, limit=limit)
        primary_key = inspect(self._model_type).primary_key
        statement = select(self._model_type).order_by(*primary_key).offset(offset).limit(limit)
        return (await self._session.execute(statement)).scalars().all()

    async def update(self, entity: ModelT) -> ModelT:
        return await self._session.merge(entity)

    async def delete(self, identifier: IdentifierT) -> bool:
        entity = await self.get(identifier)
        if entity is None:
            return False
        await self._session.delete(entity)
        await self._session.flush()
        return True

    @staticmethod
    def _validate_page(*, offset: int, limit: int) -> None:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
