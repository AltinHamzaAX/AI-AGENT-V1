from collections import Counter
from math import sqrt

from .schemas import (
    BENCHMARK_DATASET_VERSION,
    BenchmarkCategory,
    BenchmarkReview,
    CalibrationProfile,
    CalibrationStatus,
)


class HumanCalibrationEngine:
    """Measure agreement with experts; never ask the model to calibrate itself."""

    def build(
        self,
        reviews: list[BenchmarkReview],
        *,
        category: BenchmarkCategory | None = None,
        minimum_samples: int = 5,
    ) -> CalibrationProfile:
        if minimum_samples < 2:
            raise ValueError("minimum_samples must be at least 2")
        selected = [item for item in reviews if category is None or item.category is category]
        mix = Counter(item.expertise for item in selected)
        missing = []
        if len(selected) < minimum_samples:
            missing.append(f"at least {minimum_samples} reviews")
        if len(mix) < 2:
            missing.append("reviews from at least two expert disciplines")
        if missing:
            return CalibrationProfile(
                dataset_version=BENCHMARK_DATASET_VERSION,
                category=category,
                status=CalibrationStatus.INSUFFICIENT_DATA,
                sample_size=len(selected),
                minimum_samples=minimum_samples,
                reviewer_mix=dict(mix),
                missing_requirements=missing,
            )
        differences = [item.difference for item in selected]
        mean_bias = sum(differences) / len(differences)
        themes = Counter(
            review.dimension.value
            for item in selected
            for review in item.dimension_reviews
            if review.score < 8
        )
        return CalibrationProfile(
            dataset_version=BENCHMARK_DATASET_VERSION,
            category=category,
            status=CalibrationStatus.READY,
            sample_size=len(selected),
            minimum_samples=minimum_samples,
            mean_bias=round(mean_bias, 3),
            mean_absolute_error=round(sum(abs(value) for value in differences) / len(selected), 3),
            root_mean_squared_error=round(
                sqrt(sum(value * value for value in differences) / len(selected)), 3
            ),
            correlation=_correlation(selected),
            recommended_offset=round(-mean_bias, 3),
            recurring_feedback=[name for name, _ in themes.most_common(20)],
            reviewer_mix=dict(mix),
            dimension_offsets=_dimension_offsets(selected),
        )


def _correlation(reviews: list[BenchmarkReview]) -> float | None:
    human = [item.human_score for item in reviews]
    ai = [item.ai_score for item in reviews]
    human_mean = sum(human) / len(human)
    ai_mean = sum(ai) / len(ai)
    numerator = sum((x - ai_mean) * (y - human_mean) for x, y in zip(ai, human, strict=True))
    ai_spread = sum((value - ai_mean) ** 2 for value in ai)
    human_spread = sum((value - human_mean) ** 2 for value in human)
    denominator = sqrt(ai_spread * human_spread)
    return round(numerator / denominator, 3) if denominator else None


def _dimension_offsets(reviews: list[BenchmarkReview]) -> dict:
    pairs = {}
    for item in reviews:
        for human in item.dimension_reviews:
            ai_score = item.ai_dimension_scores.get(human.dimension)
            if ai_score is not None:
                pairs.setdefault(human.dimension, []).append(human.score - ai_score)
    return {
        dimension: round(sum(values) / len(values), 3)
        for dimension, values in pairs.items()
        if len(values) >= 2
    }


__all__ = ["HumanCalibrationEngine"]
