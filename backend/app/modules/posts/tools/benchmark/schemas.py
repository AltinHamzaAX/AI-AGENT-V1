from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.modules.posts.tools.quality import QualityDimension

BENCHMARK_DATASET_VERSION = "1.0"


class BenchmarkCategory(StrEnum):
    COFFEE = "coffee"
    RESTAURANT = "restaurant"
    RENT_A_CAR = "rent-a-car"
    SAAS = "saas"
    TECHNOLOGY = "technology"
    FASHION = "fashion"
    BEAUTY = "beauty"
    REAL_ESTATE = "real-estate"
    FITNESS = "fitness"
    RETAIL = "retail"
    HOSPITALITY = "hospitality"


class ReviewerExpertise(StrEnum):
    DESIGNER = "designer"
    MARKETING_EXPERT = "marketing_expert"
    CREATIVE_DIRECTOR = "creative_director"


class BenchmarkAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    required: bool = True
    preserve_identity: bool = False


class ProfessionalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    use_for: str = Field(min_length=1, max_length=500)
    copy_or_brand_source: bool = False


class QualityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: QualityDimension
    minimum_score: float = Field(ge=1, le=10)
    professional_standard: str = Field(min_length=1, max_length=600)
    failure_signals: list[str] = Field(min_length=1, max_length=12)


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    dataset_version: str = BENCHMARK_DATASET_VERSION
    category: BenchmarkCategory
    title: str = Field(min_length=1, max_length=160)
    brief: dict[str, Any]
    assets: list[BenchmarkAsset] = Field(min_length=1, max_length=20)
    professional_references: list[ProfessionalReference] = Field(min_length=1, max_length=10)
    expected_marketing_strategy: dict[str, Any]
    expected_constraints: list[str] = Field(min_length=1, max_length=30)
    quality_labels: list[QualityLabel] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def labels_are_unique(self) -> "BenchmarkCase":
        dimensions = [label.dimension for label in self.quality_labels]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("benchmark quality-label dimensions must be unique")
        return self


class HumanDimensionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: QualityDimension
    score: float = Field(ge=1, le=10)
    feedback: str = Field(min_length=1, max_length=1_000)


class HumanReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_id: UUID
    generation_id: UUID
    expertise: ReviewerExpertise
    human_score: float = Field(ge=1, le=10)
    feedback: str = Field(min_length=10, max_length=4_000)
    dimension_reviews: list[HumanDimensionReview] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def dimensions_are_unique(self) -> "HumanReviewSubmission":
        dimensions = [item.dimension for item in self.dimension_reviews]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("human dimension reviews must be unique")
        return self


class BenchmarkReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    benchmark_slug: str
    benchmark_version: str
    category: BenchmarkCategory
    generation_id: UUID
    reviewer_user_id: UUID
    project_id: UUID
    expertise: ReviewerExpertise
    human_score: float = Field(ge=1, le=10)
    ai_score: float = Field(ge=1, le=10)
    ai_dimension_scores: dict[QualityDimension, float]
    difference: float = Field(ge=-9, le=9)
    feedback: str
    dimension_reviews: list[HumanDimensionReview]
    render_checksum: str = Field(min_length=64, max_length=64)
    created_at: datetime


class CalibrationStatus(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    READY = "ready"


class CalibrationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = BENCHMARK_DATASET_VERSION
    category: BenchmarkCategory | None = None
    status: CalibrationStatus
    sample_size: int = Field(ge=0)
    minimum_samples: int = Field(ge=2)
    mean_bias: float | None = None
    mean_absolute_error: float | None = None
    root_mean_squared_error: float | None = None
    correlation: float | None = Field(default=None, ge=-1, le=1)
    recommended_offset: float | None = Field(default=None, ge=-9, le=9)
    recurring_feedback: list[str] = Field(default_factory=list, max_length=20)
    reviewer_mix: dict[ReviewerExpertise, int] = Field(default_factory=dict)
    dimension_offsets: dict[QualityDimension, float] = Field(default_factory=dict)
    missing_requirements: list[str] = Field(default_factory=list, max_length=10)

    def calibrate(self, ai_score: float) -> float:
        if self.status is not CalibrationStatus.READY or self.recommended_offset is None:
            raise ValueError("calibration profile is not ready")
        return round(min(10.0, max(1.0, ai_score + self.recommended_offset)), 2)


__all__ = [
    "BENCHMARK_DATASET_VERSION", "BenchmarkAsset", "BenchmarkCase", "BenchmarkCategory",
    "BenchmarkReview", "CalibrationProfile", "CalibrationStatus", "HumanDimensionReview",
    "HumanReviewSubmission", "ProfessionalReference", "QualityLabel", "ReviewerExpertise",
]
