"""Persistence contracts shared by application modules."""

from app.repositories.base import Repository
from app.repositories.unit_of_work import UnitOfWork

__all__ = ["Repository", "UnitOfWork"]
