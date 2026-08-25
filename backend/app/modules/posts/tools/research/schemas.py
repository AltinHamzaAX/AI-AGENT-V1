import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

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
    #: The category could not be researched. Distinct from NO_RESULTS, which
    #: means the provider answered and had nothing: absence of evidence is not
    #: the same claim as absence of research, and downstream stages must be
    #: able to tell them apart.
    FAILED = "failed"


class ResearchSourceType(StrEnum):
    GOVERNMENT = "government"
    OFFICIAL_PLATFORM = "official_platform"
    BUSINESS_OR_ORGANIZATION = "business_or_organization"
    INDUSTRY_REPORT = "industry_report"
    NEWS_OR_EDITORIAL = "news_or_editorial"
    SOCIAL_POST = "social_post"
    MARKETPLACE = "marketplace"
    BLOG_OR_GUIDE = "blog_or_guide"
    UNKNOWN = "unknown"


class EvidenceCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


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


#: How much of a source is kept on the report.
SOURCE_EXCERPT_LIMIT = 4_000
#: How much of each excerpt the analyzer is shown. Evidence quotes must appear
#: in the excerpt, so this bounds what the model is able to cite. The span is
#: chosen by relevance rather than taken from the front of the page, which is
#: what makes it affordable: measured over ten live results, 1,200 ranked
#: characters carried more price and market signal than the first 2,000 did,
#: and cost the prompt 40% fewer characters per source.
ANALYSIS_EXCERPT_LIMIT = 1_200
#: How many sources the analyzer is shown, best-quality first. The report still
#: keeps every source it found; this only bounds one model call. Measured
#: against a local 7B model, twenty sources took 108s and ten took 71s, so the
#: full set sat on top of the provider timeout with no headroom for variance.
ANALYSIS_MAX_SOURCES = 12


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1_000)
    url: HttpUrl
    excerpt: str = Field(min_length=1, max_length=SOURCE_EXCERPT_LIMIT)
    provider_score: float | None = Field(default=None, ge=0, le=1)
    retrieved_at: datetime
    published_at: datetime | None = None
    source_type: ResearchSourceType = ResearchSourceType.UNKNOWN
    authority_score: float = Field(default=0.4, ge=0, le=1)
    locality_score: float = Field(default=0.3, ge=0, le=1)
    #: None when the provider gave no publication date.
    freshness_score: float | None = Field(default=None, ge=0, le=1)
    quality_score: float = Field(default=0.4, ge=0, le=1)
    confidence: ResearchConfidence = ResearchConfidence.LOW
    dimensions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("dimensions")
    @classmethod
    def dimensions_must_be_unique(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_text(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("research source dimensions must be unique")
        return normalized


class ResearchVisualReference(BaseModel):
    """An observed image, kept as a reference rather than an asset.

    Research collects what the market looks like; Art Direction decides what
    this post looks like. Nothing here is a design instruction, and the image
    is referenced by URL — it is never fetched, stored, or reproduced.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    description: str | None = Field(default=None, min_length=1, max_length=1_000)
    retrieved_at: datetime

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return None if value is None else _normalized_text(value)


#: Findings quote a bounded lead extract rather than the whole source excerpt.
#: The full text already lives once on the source; copying it verbatim into
#: every finding doubled a payload that is written to workflow state, cached in
#: Redis, and pasted into downstream prompts.
FINDING_EXTRACT_LIMIT = 400


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=FINDING_EXTRACT_LIMIT)
    source_url: HttpUrl
    confidence: ResearchConfidence
    authority: str = Field(default="external_evidence", pattern="^external_evidence$")


#: Longest evidence span kept on a report. A quote is a pointer into a source,
#: not a copy of it.
EVIDENCE_QUOTE_LIMIT = 500


class ResearchEvidenceQuote(BaseModel):
    """A verbatim span from one source, with an optional English gloss.

    `quote` stays in the source language because it is verified character for
    character against the source excerpt; translating it would destroy the
    grounding check. `translation` carries the English meaning for downstream
    agents, which operate in English.
    """

    model_config = ConfigDict(extra="forbid")

    source_url: HttpUrl
    quote: str = Field(min_length=8, max_length=EVIDENCE_QUOTE_LIMIT)
    translation: str | None = Field(default=None, min_length=1, max_length=EVIDENCE_QUOTE_LIMIT)

    @field_validator("quote")
    @classmethod
    def normalize_quote(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("translation")
    @classmethod
    def normalize_translation(cls, value: str | None) -> str | None:
        return None if value is None else _normalized_text(value)


class ResearchInsight(BaseModel):
    """A bounded observation grounded only in sources from the same report."""

    model_config = ConfigDict(extra="forbid")

    observation: str = Field(min_length=1, max_length=2_000)
    source_urls: list[HttpUrl] = Field(min_length=1, max_length=10)
    evidence: list[ResearchEvidenceQuote] = Field(min_length=1, max_length=10)
    confidence: ResearchConfidence
    authority: Literal["external_evidence"] = "external_evidence"

    @field_validator("observation")
    @classmethod
    def normalize_observation(cls, value: str) -> str:
        return _normalized_text(value)

    @field_validator("source_urls")
    @classmethod
    def source_urls_must_be_unique(cls, values: list[HttpUrl]) -> list[HttpUrl]:
        if len({str(value) for value in values}) != len(values):
            raise ValueError("research insight source URLs must be unique")
        return values

    @model_validator(mode="after")
    def evidence_must_cover_declared_sources(self) -> "ResearchInsight":
        urls = {str(value) for value in self.source_urls}
        evidence_urls = {str(value.source_url) for value in self.evidence}
        if urls != evidence_urls:
            raise ValueError("research insight evidence must cover its source URLs")
        return self


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_dimensions: list[str] = Field(min_length=1, max_length=20)
    covered_dimensions: list[str] = Field(default_factory=list, max_length=20)
    missing_dimensions: list[str] = Field(default_factory=list, max_length=20)
    coverage_ratio: float = Field(ge=0, le=1)
    mean_source_quality: float = Field(ge=0, le=1)
    status: EvidenceCoverageStatus
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def dimensions_and_ratio_are_consistent(self) -> "EvidenceCoverage":
        required = set(self.required_dimensions)
        covered = set(self.covered_dimensions)
        missing = set(self.missing_dimensions)
        if covered | missing != required or covered & missing:
            raise ValueError("research coverage dimensions are inconsistent")
        expected_ratio = len(covered) / len(required)
        if abs(self.coverage_ratio - expected_ratio) > 0.001:
            raise ValueError("research coverage ratio is inconsistent")
        expected_status = (
            EvidenceCoverageStatus.COMPLETE
            if expected_ratio == 1
            else EvidenceCoverageStatus.PARTIAL
            if expected_ratio >= 0.5
            else EvidenceCoverageStatus.INSUFFICIENT
        )
        if self.status is not expected_status:
            raise ValueError("research coverage status is inconsistent")
        return self


class MarketResearchAnalysis(BaseModel):
    """Market evidence only; downstream Marketing Strategy owns decisions."""

    model_config = ConfigDict(extra="forbid")

    category: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    market_expectations: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    offers: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    customer_expectations: list[ResearchInsight] = Field(
        default_factory=list,
        max_length=10,
    )
    positioning_patterns: list[ResearchInsight] = Field(
        default_factory=list,
        max_length=10,
    )
    opportunities: list[ResearchInsight] = Field(default_factory=list, max_length=10)


class CompetitorResearchAnalysis(BaseModel):
    """Competitor observations for differentiation, never replication."""

    model_config = ConfigDict(extra="forbid")

    messaging: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    offers: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    cta: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    visual_language: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    differentiation: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    overused_patterns: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    safe_use: Literal["differentiate_do_not_copy"] = "differentiate_do_not_copy"

    @model_validator(mode="after")
    def require_safe_evidence(self) -> "CompetitorResearchAnalysis":
        insights = _analysis_insights(self)
        prohibited = (
            "copy this",
            "copy the competitor",
            "imitate this",
            "replicate this",
            "use the same",
            "match the competitor",
            "kopjo këtë",
            "kopjo konkurrentin",
        )
        for insight in insights:
            normalized = insight.observation.casefold()
            if any(marker in normalized for marker in prohibited):
                raise ValueError("competitor research cannot instruct downstream copying")
        return self


class SocialResearchAnalysis(BaseModel):
    """Observed social-platform patterns, not creative direction."""

    model_config = ConfigDict(extra="forbid")

    platform_creative_patterns: list[ResearchInsight] = Field(
        default_factory=list,
        max_length=10,
    )
    text_density: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    cta: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    logo_placement: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    photography: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    graphic_systems: list[ResearchInsight] = Field(default_factory=list, max_length=10)
    compositions: list[ResearchInsight] = Field(default_factory=list, max_length=10)


ResearchAnalysis = MarketResearchAnalysis | CompetitorResearchAnalysis | SocialResearchAnalysis


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
    visual_references: list[ResearchVisualReference] = Field(
        default_factory=list,
        max_length=20,
    )
    analysis: ResearchAnalysis | None = None
    evidence_coverage: EvidenceCoverage | None = None
    researched_at: datetime
    expires_at: datetime
    cache_key: str = Field(min_length=64, max_length=64)
    cached: bool = False
    #: Safe failure category for FAILED reports. Never a provider message,
    #: response body, prompt, or credential.
    error: str | None = Field(default=None, min_length=1, max_length=200)
    #: Dimensions whose own search failed. Their absence from the analysis is a
    #: search failure, not an observation that the market is empty.
    degraded_dimensions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("degraded_dimensions")
    @classmethod
    def degraded_dimensions_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("degraded research dimensions must be unique")
        return values

    @field_validator("findings", "sources", "visual_references")
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
        if self.status is ResearchStatus.SUCCEEDED and not (self.sources or self.visual_references):
            raise ValueError("successful research must contain a source or a visual reference")
        if self.status is not ResearchStatus.SUCCEEDED:
            if self.sources or self.findings or self.visual_references or self.analysis is not None:
                raise ValueError("research without results cannot contain evidence")
            if self.confidence is not ResearchConfidence.LOW:
                raise ValueError("research without results must have low confidence")
        if self.status is ResearchStatus.FAILED and self.error is None:
            raise ValueError("failed research must record a safe error code")
        if self.status is not ResearchStatus.FAILED and self.error is not None:
            raise ValueError("only failed research may record an error code")
        source_urls = {str(source.url) for source in self.sources}
        missing = [
            str(finding.source_url)
            for finding in self.findings
            if str(finding.source_url) not in source_urls
        ]
        if missing:
            raise ValueError("every research finding must reference a report source")
        expected_analysis = {
            ResearchCategory.MARKET: MarketResearchAnalysis,
            ResearchCategory.COMPETITOR: CompetitorResearchAnalysis,
            ResearchCategory.SOCIAL: SocialResearchAnalysis,
        }.get(self.category)
        if self.analysis is not None and (
            expected_analysis is None or not isinstance(self.analysis, expected_analysis)
        ):
            raise ValueError("research analysis does not match the report category")
        cited_urls = {
            str(url) for insight in _analysis_insights(self.analysis) for url in insight.source_urls
        }
        if cited_urls - source_urls:
            raise ValueError("every research insight must reference a report source")
        sources_by_url = {str(source.url): source for source in self.sources}
        for insight in _analysis_insights(self.analysis):
            for evidence in insight.evidence:
                source = sources_by_url[str(evidence.source_url)]
                if comparable_text(evidence.quote) not in comparable_text(
                    source_evidence_text(source)
                ):
                    raise ValueError("research evidence quote must exist in its source")
        if self.analysis is not None and self.evidence_coverage is None:
            raise ValueError("structured research requires evidence coverage")
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
    "EvidenceCoverage",
    "EvidenceCoverageStatus",
    "CompetitorResearchAnalysis",
    "MarketResearchAnalysis",
    "ResearchCategory",
    "ResearchAnalysis",
    "ResearchConfidence",
    "ResearchContext",
    "ResearchFinding",
    "ResearchEvidenceQuote",
    "ResearchInsight",
    "ResearchReport",
    "ResearchSource",
    "ResearchSourceType",
    "ResearchStatus",
    "SocialResearchAnalysis",
]


def _analysis_insights(value: BaseModel | None) -> list[ResearchInsight]:
    if value is None:
        return []
    insights: list[ResearchInsight] = []
    for field_name in type(value).model_fields:
        field_value = getattr(value, field_name)
        if isinstance(field_value, list):
            insights.extend(item for item in field_value if isinstance(item, ResearchInsight))
    return insights


#: Typographic variants that a page and a model spell differently while meaning
#: exactly the same characters.
_TYPOGRAPHY = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "―": "-", "−": "-",
        "​": "", "‌": "", "‍": "", "﻿": "",
    }
)


def source_evidence_text(source: "ResearchSource") -> str:
    """Everything a quote may legitimately come from for one source.

    The title is retrieved text like any other, it is handed to the analyzer
    alongside the excerpt, and it is often the single most informative line on
    the page. Verifying against the excerpt alone rejected correct quotations
    of it.
    """
    return f"{source.title} {source.excerpt}"


def comparable_text(value: str) -> str:
    """Fold a span to the form used when checking a quote against its source.

    Pages are full of curly quotes, en dashes and non-breaking spaces that a
    model reproduces as their plain equivalents. Comparing raw characters
    rejected correct quotations, so both sides are folded first. This only
    removes typographic difference: a paraphrase still fails to match.
    """
    folded = unicodedata.normalize("NFKC", value).translate(_TYPOGRAPHY)
    return " ".join(folded.split()).casefold()


def _normalized_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text cannot be blank")
    return normalized
