from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.posts.domain.entities import PostScope
from app.modules.posts.tools.benchmark import BenchmarkCategory, BenchmarkReview, ReviewerExpertise


class BenchmarkReviewRepository(Protocol):
    async def add(
        self,
        *,
        benchmark_slug: str,
        benchmark_version: str,
        category: BenchmarkCategory,
        generation_id: UUID,
        scope: PostScope,
        expertise: ReviewerExpertise,
        human_score: float,
        ai_score: float,
        ai_dimension_scores: dict[str, float],
        feedback: str,
        dimension_reviews: list[dict],
        render_checksum: str,
    ) -> BenchmarkReview: ...

    async def exists(
        self,
        *,
        benchmark_slug: str,
        benchmark_version: str,
        generation_id: UUID,
        reviewer_user_id: UUID,
    ) -> bool: ...

    async def list(
        self,
        *,
        category: BenchmarkCategory | None = None,
        benchmark_version: str | None = None,
    ) -> Sequence[BenchmarkReview]: ...


__all__ = ["BenchmarkReviewRepository"]
