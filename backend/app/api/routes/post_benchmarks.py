from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies.posts import BenchmarkServiceDependency, PostScopeDependency
from app.modules.posts.domain.exceptions import (
    BenchmarkCaseNotFoundError,
    BenchmarkGenerationNotReadyError,
    BenchmarkReviewConflictError,
    PostGenerationNotFoundError,
)
from app.modules.posts.tools.benchmark import (
    BenchmarkCase,
    BenchmarkCategory,
    BenchmarkReview,
    CalibrationProfile,
    HumanReviewSubmission,
)

router = APIRouter()


@router.get("", response_model=list[BenchmarkCase])
async def list_benchmark_cases(
    service: BenchmarkServiceDependency,
    category: BenchmarkCategory | None = None,
) -> list[BenchmarkCase]:
    return list(service.list_cases(category=category))


@router.get("/calibration", response_model=CalibrationProfile)
async def get_calibration_profile(
    service: BenchmarkServiceDependency,
    category: BenchmarkCategory | None = None,
    minimum_samples: int = Query(default=5, ge=2, le=500),
) -> CalibrationProfile:
    return await service.calibration_profile(
        category=category, minimum_samples=minimum_samples
    )


@router.get("/{benchmark_slug}", response_model=BenchmarkCase)
async def get_benchmark_case(
    benchmark_slug: str,
    service: BenchmarkServiceDependency,
) -> BenchmarkCase:
    try:
        return service.get_case(benchmark_slug)
    except BenchmarkCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Benchmark case not found") from exc


@router.post(
    "/{benchmark_slug}/reviews",
    response_model=BenchmarkReview,
    status_code=status.HTTP_201_CREATED,
)
async def submit_benchmark_review(
    benchmark_slug: str,
    payload: HumanReviewSubmission,
    scope: PostScopeDependency,
    service: BenchmarkServiceDependency,
) -> BenchmarkReview:
    try:
        return await service.submit_review(
            benchmark_slug=benchmark_slug, submission=payload, scope=scope
        )
    except BenchmarkCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Benchmark case not found") from exc
    except PostGenerationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Post generation not found") from exc
    except BenchmarkGenerationNotReadyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generation has no completed quality approval to calibrate",
        ) from exc
    except BenchmarkReviewConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="This reviewer already calibrated this generation against this benchmark",
        ) from exc


__all__ = ["router"]
