from app.modules.posts.domain.entities import PostScope
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.exceptions import (
    BenchmarkCaseNotFoundError,
    BenchmarkGenerationNotReadyError,
    BenchmarkReviewConflictError,
)
from app.modules.posts.repositories.benchmark import BenchmarkReviewRepository
from app.modules.posts.services.posts import PostsService
from app.modules.posts.tools.benchmark import (
    BENCHMARK_DATASET_VERSION,
    BenchmarkCase,
    BenchmarkCatalog,
    BenchmarkCategory,
    BenchmarkReview,
    CalibrationProfile,
    HumanCalibrationEngine,
    HumanReviewSubmission,
)
from app.modules.posts.tools.quality import QualityApprovalReport


class BenchmarkService:
    def __init__(
        self,
        repository: BenchmarkReviewRepository,
        posts: PostsService,
        *,
        catalog: BenchmarkCatalog | None = None,
        calibration: HumanCalibrationEngine | None = None,
    ) -> None:
        self._repository = repository
        self._posts = posts
        self._catalog = catalog or BenchmarkCatalog()
        self._calibration = calibration or HumanCalibrationEngine()

    def list_cases(
        self, *, category: BenchmarkCategory | None = None
    ) -> tuple[BenchmarkCase, ...]:
        return self._catalog.list(category=category)

    def get_case(self, slug: str) -> BenchmarkCase:
        case = self._catalog.get(slug)
        if case is None:
            raise BenchmarkCaseNotFoundError
        return case

    async def submit_review(
        self,
        *,
        benchmark_slug: str,
        submission: HumanReviewSubmission,
        scope: PostScope,
    ) -> BenchmarkReview:
        case = self.get_case(benchmark_slug)
        state = await self._posts.get_workflow_state(
            generation_id=submission.generation_id,
            post_id=submission.post_id,
            scope=scope,
        )
        raw_report = state.data[PostWorkflowSection.QUALITY_APPROVAL.value]
        if not raw_report:
            raise BenchmarkGenerationNotReadyError
        report = QualityApprovalReport.model_validate(raw_report)
        if await self._repository.exists(
            benchmark_slug=case.slug,
            benchmark_version=case.dataset_version,
            generation_id=submission.generation_id,
            reviewer_user_id=scope.user_id,
        ):
            raise BenchmarkReviewConflictError
        return await self._repository.add(
            benchmark_slug=case.slug,
            benchmark_version=case.dataset_version,
            category=case.category,
            generation_id=submission.generation_id,
            scope=scope,
            expertise=submission.expertise,
            human_score=submission.human_score,
            # Never trust an AI score from the client. This is the exact score
            # attached to the render that the expert reviewed.
            ai_score=report.overall_score,
            ai_dimension_scores={
                item.dimension.value: item.score for item in report.scores
            },
            feedback=submission.feedback,
            dimension_reviews=[
                item.model_dump(mode="json") for item in submission.dimension_reviews
            ],
            render_checksum=report.render_checksum,
        )

    async def calibration_profile(
        self,
        *,
        category: BenchmarkCategory | None = None,
        minimum_samples: int = 5,
    ) -> CalibrationProfile:
        reviews = list(
            await self._repository.list(
                category=category,
                benchmark_version=BENCHMARK_DATASET_VERSION,
            )
        )
        return self._calibration.build(
            reviews, category=category, minimum_samples=minimum_samples
        )


__all__ = ["BenchmarkService"]
