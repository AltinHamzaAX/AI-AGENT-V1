from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posts import PostBenchmarkReviewModel
from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.exceptions import BenchmarkReviewConflictError
from app.modules.posts.tools.benchmark import (
    BenchmarkCategory,
    BenchmarkReview,
    HumanDimensionReview,
    ReviewerExpertise,
)


def _review(model: PostBenchmarkReviewModel) -> BenchmarkReview:
    return BenchmarkReview(
        id=model.id,
        benchmark_slug=model.benchmark_slug,
        benchmark_version=model.benchmark_version,
        category=BenchmarkCategory(model.category),
        generation_id=model.generation_id,
        reviewer_user_id=model.reviewer_user_id,
        project_id=model.project_id,
        expertise=ReviewerExpertise(model.expertise),
        human_score=float(model.human_score),
        ai_score=float(model.ai_score),
        ai_dimension_scores=model.ai_dimension_scores,
        difference=float(model.score_difference),
        feedback=model.feedback,
        dimension_reviews=[
            HumanDimensionReview.model_validate(item) for item in model.dimension_reviews
        ],
        render_checksum=model.render_checksum,
        created_at=model.created_at,
    )


class SQLAlchemyBenchmarkReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> BenchmarkReview:
        model = PostBenchmarkReviewModel(
            benchmark_slug=benchmark_slug,
            benchmark_version=benchmark_version,
            category=category.value,
            generation_id=generation_id,
            reviewer_user_id=scope.user_id,
            project_id=scope.project_id,
            expertise=expertise.value,
            human_score=Decimal(str(round(human_score, 2))),
            ai_score=Decimal(str(round(ai_score, 2))),
            ai_dimension_scores=ai_dimension_scores,
            score_difference=Decimal(str(round(ai_score - human_score, 2))),
            feedback=feedback,
            dimension_reviews=dimension_reviews,
            render_checksum=render_checksum,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise BenchmarkReviewConflictError from exc
        await self._session.refresh(model)
        return _review(model)

    async def exists(
        self,
        *,
        benchmark_slug: str,
        benchmark_version: str,
        generation_id: UUID,
        reviewer_user_id: UUID,
    ) -> bool:
        statement = select(PostBenchmarkReviewModel.id).where(
            PostBenchmarkReviewModel.benchmark_slug == benchmark_slug,
            PostBenchmarkReviewModel.benchmark_version == benchmark_version,
            PostBenchmarkReviewModel.generation_id == generation_id,
            PostBenchmarkReviewModel.reviewer_user_id == reviewer_user_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def list(
        self,
        *,
        category: BenchmarkCategory | None = None,
        benchmark_version: str | None = None,
    ) -> tuple[BenchmarkReview, ...]:
        statement = select(PostBenchmarkReviewModel)
        if category is not None:
            statement = statement.where(PostBenchmarkReviewModel.category == category.value)
        if benchmark_version is not None:
            statement = statement.where(
                PostBenchmarkReviewModel.benchmark_version == benchmark_version
            )
        statement = statement.order_by(
            PostBenchmarkReviewModel.created_at, PostBenchmarkReviewModel.id
        )
        models = (await self._session.execute(statement)).scalars().all()
        return tuple(_review(model) for model in models)


__all__ = ["SQLAlchemyBenchmarkReviewRepository"]
