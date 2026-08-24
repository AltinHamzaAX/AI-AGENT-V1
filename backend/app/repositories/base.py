from collections.abc import Sequence
from typing import Protocol, TypeVar

EntityT = TypeVar("EntityT")
IdentifierT = TypeVar("IdentifierT")


class Repository(Protocol[EntityT, IdentifierT]):
    """Persistence-agnostic CRUD contract for application and domain layers."""

    async def add(self, entity: EntityT) -> EntityT: ...

    async def get(self, identifier: IdentifierT) -> EntityT | None: ...

    async def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[EntityT]: ...

    async def update(self, entity: EntityT) -> EntityT: ...

    async def delete(self, identifier: IdentifierT) -> bool: ...
