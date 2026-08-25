from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.modules.posts.agents.audience_research import AudienceIntelligence


class ResearchCategory(StrEnum):
    MARKET = "market"
    COMPETITOR = "competitor"
    AUDIENCE = "audience"
    SOCIAL = "social"
    VISUAL_REFERENCE = "visual_reference"
    TREND = "trend"
    PLATFORM = "platform"
    BRAND_PRODUCT = "brand_product"


class ResearchConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResearchStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NO_RESULTS = "no_results"


class ExternalResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, Any]
    audience: AudienceIntelligence


class ResearchContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str | None
    brand: str | None
    product: str | None
    primary_entity: str
    audience: str
    target_segment: str
    market: str | None
    location: str | None
    platform: str
    language: str
    required_facts: dict[str, str]
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1_000)
    url: HttpUrl
    excerpt: str = Field(min_length=1, max_length=4_000)
    provider_score: float | None = Field(default=None, ge=0, le=1)
    retrieved_at: datetime


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4_000)
    source_url: HttpUrl
    confidence: ResearchConfidence
    authority: str = Field(default="external_evidence", pattern="^external_evidence$")


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ResearchCategory
    status: ResearchStatus
    query: str = Field(min_length=1, max_length=2_000)
    provider: str = Field(min_length=1, max_length=100)
    provider_summary: str | None = Field(default=None, max_length=8_000)
    confidence: ResearchConfidence
    findings: list[ResearchFinding] = Field(default_factory=list, max_length=20)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=20)
    researched_at: datetime
    expires_at: datetime
    cache_key: str = Field(min_length=64, max_length=64)
    cached: bool = False

    @field_validator("findings", "sources")
    @classmethod
    def values_must_be_unique(cls, values: list[Any]) -> list[Any]:
        serialized = [str(value) for value in values]
        if len(serialized) != len(set(serialized)):
            raise ValueError("research report values must be unique")
        return values

    @model_validator(mode="after")
    def evidence_and_timestamps_are_consistent(self) -> "ResearchReport":
        if self.researched_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("research timestamps must be timezone-aware")
        if self.expires_at <= self.researched_at:
            raise ValueError("research expiry must be after retrieval")
        if self.status is ResearchStatus.SUCCEEDED and not self.sources:
            raise ValueError("successful research must contain a source")
        if self.status is ResearchStatus.NO_RESULTS:
            if self.sources or self.findings:
                raise ValueError("no-results research cannot contain evidence")
            if self.confidence is not ResearchConfidence.LOW:
                raise ValueError("no-results research must have low confidence")
        source_urls = {str(source.url) for source in self.sources}
        missing = [
            str(finding.source_url)
            for finding in self.findings
            if str(finding.source_url) not in source_urls
        ]
        if missing:
            raise ValueError("every research finding must reference a report source")
        return self


class ExternalResearchResult(BaseModel):
    """Evidence only. Positioning, strategy, copy, and creative fields are absent."""

    model_config = ConfigDict(extra="forbid")

    market: ResearchReport
    competitor: ResearchReport
    audience: ResearchReport
    social: ResearchReport
    visual_reference: ResearchReport
    trend: ResearchReport
    platform: ResearchReport
    brand_product: ResearchReport
    researched_at: datetime
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def report_categories_are_fixed(self) -> "ExternalResearchResult":
        for category in ResearchCategory:
            report = getattr(self, category.value)
            if report.category is not category:
                raise ValueError(f"{category.value} report has the wrong category")
        if self.researched_at.tzinfo is None:
            raise ValueError("external research timestamp must be timezone-aware")
        return self


__all__ = [
    "ExternalResearchInput",
    "ExternalResearchResult",
    "ResearchCategory",
    "ResearchConfidence",
    "ResearchContext",
    "ResearchFinding",
    "ResearchReport",
    "ResearchSource",
    "ResearchStatus",
]
