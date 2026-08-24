from typing import Protocol

from app.modules.posts.repositories import GenerationJobRepository


class JobQueue(GenerationJobRepository, Protocol):
    """Durable queue port; PostgreSQL leases are the current adapter."""
