from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.audience_research import AudienceIntelligence
from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.tools.creative import CreativeDNA
from app.modules.posts.tools.research import ExternalResearchResult


class CreativeAngle(StrEnum):
    """The lens a territory looks through.

    Territories are only genuinely different when they enter the brief from
    different directions. Naming the lens on the territory makes sameness
    checkable instead of a matter of taste: three routes that all argue "fast
    and reliable" can no longer be presented as an exploration.
    """

    EMOTIONAL_TRANSFORMATION = "emotional_transformation"
    VISUAL_METAPHOR = "visual_metaphor"
    CULTURAL_TENSION = "cultural_tension"
    PRODUCT_DEMONSTRATION = "product_demonstration"
    BRAND_SYMBOL = "brand_symbol"



CONCEPT_SELECTION_DIMENSIONS = (
    "strategy_fit",
    "audience_fit",
    "brand_fit",
    "originality",
    "clarity",
    "visual_potential",
    "platform_fit",
    "production_feasibility",
)

#: The bar a selected concept has to clear. Ticket 24's differentiation,
#: safety and hook-alignment checks remain gates rather than ranking criteria.
QUALITY_THRESHOLDS: dict[str, int] = {
    "strategy_fit": 8,
    "audience_fit": 8,
    "brand_fit": 8,
    "originality": 8,
    "clarity": 8,
    "visual_potential": 8,
    "platform_fit": 8,
    "production_feasibility": 8,
    "territory_differentiation": 8,
    "claim_safety": 10,
    "concept_hook_alignment": 9,
}


class CreativeDirectorInput(BaseModel):
    """Approved strategic inputs; no design or production state is accepted."""

    model_config = ConfigDict(extra="forbid")

    marketing_strategy: MarketingStrategy
    audience: AudienceIntelligence
    brand: BrandAnalysis
    research: ExternalResearchResult
    semantic_contract: dict[str, Any]
    rejected_concept_memory: list[str] = Field(default_factory=list, max_length=20)
    recent_creative_patterns: list[CreativeDNA] = Field(default_factory=list, max_length=20)

    @field_validator("recent_creative_patterns")
    @classmethod
    def recent_patterns_must_be_unique(cls, values: list[CreativeDNA]) -> list[CreativeDNA]:
        if len({item.fingerprint for item in values}) != len(values):
            raise ValueError("recent creative patterns must be unique")
        return values

    @field_validator("rejected_concept_memory")
    @classmethod
    def normalize_rejected_memory(cls, values: list[str]) -> list[str]:
        return _unique(values)

    @model_validator(mode="after")
    def inputs_must_describe_one_post(self) -> "CreativeDirectorInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("creative director requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.marketing_strategy.contract_fingerprint,
            self.audience.contract_fingerprint,
            self.brand.contract_fingerprint,
            self.research.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("creative director inputs disagree on the semantic contract")
        return self


class CreativeEvaluation(BaseModel):
    """A scorecard that has to cost something.

    Scores are useless when every candidate is excellent, so the schema itself
    refuses a flawless card and requires the weakness to be written down. The
    dimensions are the ones the selected concept is later gated on.
    """

    model_config = ConfigDict(extra="forbid")

    strategy_fit: int = Field(ge=1, le=10)
    audience_fit: int = Field(ge=1, le=10)
    brand_fit: int = Field(ge=1, le=10)
    originality: int = Field(ge=1, le=10)
    clarity: int = Field(ge=1, le=10)
    visual_potential: int = Field(ge=1, le=10)
    platform_fit: int = Field(ge=1, le=10)
    production_feasibility: int = Field(ge=1, le=10)
    territory_differentiation: int = Field(ge=1, le=10)
    claim_safety: int = Field(ge=1, le=10)
    concept_hook_alignment: int = Field(ge=1, le=10)
    weakness: str = Field(min_length=1, max_length=500)

    @field_validator("weakness")
    @classmethod
    def normalize_weakness(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def scorecard_must_admit_a_tradeoff(self) -> "CreativeEvaluation":
        if all(score == 10 for score in self.scores().values()):
            raise ValueError("a flawless scorecard is not a credible creative evaluation")
        return self

    def craft_scores(self) -> dict[str, int]:
        """Every dimension that expresses judgement rather than compliance."""
        return {
            name: getattr(self, name) for name in QUALITY_THRESHOLDS if name != "claim_safety"
        }

    def selection_scores(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in CONCEPT_SELECTION_DIMENSIONS}

    def scores(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in QUALITY_THRESHOLDS}

    @property
    def total(self) -> int:
        return sum(self.selection_scores().values())


class CreativeTerritory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^territory_[1-5]$")
    angle: CreativeAngle
    name: str = Field(min_length=1, max_length=160)
    premise: str = Field(min_length=1, max_length=750)
    creative_tension: str = Field(min_length=1, max_length=750)
    #: How the approved marketing angle is reinterpreted here. Required so the
    #: causal chain can be checked for interpretation rather than restatement.
    strategic_link: str = Field(min_length=1, max_length=750)
    mood: list[str] = Field(min_length=2, max_length=8)
    rationale: str = Field(min_length=1, max_length=1_000)
    basis: list[str] = Field(min_length=2, max_length=20)

    @field_validator("name", "premise", "creative_tension", "strategic_link", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _text(value)

    @field_validator("mood", "basis")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _unique(values)


class VisualHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^hook_[1-5]$")
    description: str = Field(min_length=1, max_length=750)
    #: The single object, gesture or transformation the image turns on.
    symbol: str = Field(min_length=1, max_length=300)
    #: What the image says with every word removed. A hook that only works once
    #: a caption explains it is a layout note, not a hook.
    wordless_read: str = Field(min_length=1, max_length=500)
    mechanism: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1_000)
    basis: list[str] = Field(min_length=2, max_length=20)

    @field_validator("description", "symbol", "wordless_read", "mechanism", "rationale")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _text(value)

    @field_validator("basis")
    @classmethod
    def normalize_basis(cls, values: list[str]) -> list[str]:
        return _unique(values)


class BigIdeaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^idea_[1-5]$")
    name: str = Field(min_length=1, max_length=160)
    idea: str = Field(min_length=1, max_length=750)
    territory_id: str = Field(pattern=r"^territory_[1-5]$")
    visual_hook_id: str = Field(pattern=r"^hook_[1-5]$")
    #: What this idea adds to its territory, and how the hook carries it. Both
    #: links keep the chain interpretive instead of a chain of synonyms.
    territory_link: str = Field(min_length=1, max_length=750)
    hook_link: str = Field(min_length=1, max_length=750)
    #: Further executions the same idea would carry. An idea that only supports
    #: the post in front of it is an advertisement, not a Big Idea.
    extensions: list[str] = Field(min_length=2, max_length=6)
    #: Feasibility with approved assets, stated for the Art Director without
    #: becoming the layout itself.
    production_notes: str = Field(min_length=1, max_length=750)
    rationale: str = Field(min_length=1, max_length=1_000)
    basis: list[str] = Field(min_length=2, max_length=20)
    evaluation: CreativeEvaluation

    @field_validator(
        "name",
        "idea",
        "territory_link",
        "hook_link",
        "production_notes",
        "rationale",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _text(value)

    @field_validator("extensions", "basis")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _unique(values)


class QualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1, max_length=80)
    score: int = Field(ge=1, le=10)
    threshold: int = Field(ge=1, le=10)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


class CreativeQualityGate(BaseModel):
    """The published record of what the selected concept was held to."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^idea_[1-5]$")
    checks: list[QualityCheck] = Field(min_length=1, max_length=12)

    @property
    def failures(self) -> list[QualityCheck]:
        return [check for check in self.checks if not check.passed]


class WinningConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^idea_[1-5]$")
    rank: int = Field(default=1, ge=1, le=1)
    total_score: int = Field(ge=8, le=80)
    rationale: str = Field(min_length=1, max_length=2_000)


class RejectedConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^idea_[1-5]$")
    rank: int = Field(ge=2, le=5)
    total_score: int = Field(ge=8, le=80)
    rejection_reason: str = Field(min_length=1, max_length=1_000)
    weakness: str = Field(min_length=1, max_length=500)


class CreativeDirectorLLMOutput(BaseModel):
    """Exploration only; final selection is computed by the application."""

    model_config = ConfigDict(extra="forbid")

    creative_territories: list[CreativeTerritory] = Field(min_length=3, max_length=5)
    visual_hooks: list[VisualHook] = Field(min_length=3, max_length=5)
    big_idea_candidates: list[BigIdeaCandidate] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def references_must_be_complete_and_unique(self) -> "CreativeDirectorLLMOutput":
        _require_unique([item.id for item in self.creative_territories], "territory IDs")
        _require_unique([item.id for item in self.visual_hooks], "visual hook IDs")
        _require_unique([item.id for item in self.big_idea_candidates], "big idea IDs")
        _require_unique(
            [item.angle.value for item in self.creative_territories],
            "creative territory angles",
        )
        territory_ids = {item.id for item in self.creative_territories}
        hook_ids = {item.id for item in self.visual_hooks}
        for candidate in self.big_idea_candidates:
            if candidate.territory_id not in territory_ids:
                raise ValueError("big idea references an unknown creative territory")
            if candidate.visual_hook_id not in hook_ids:
                raise ValueError("big idea references an unknown visual hook")
        return self


class CreativeDirection(CreativeDirectorLLMOutput):
    model_config = ConfigDict(extra="forbid")

    winning_concept: WinningConcept
    rejected_concepts: list[RejectedConcept] = Field(min_length=2, max_length=4)
    creative_rationale: str = Field(min_length=1, max_length=2_000)
    quality_gate: CreativeQualityGate
    limitations: list[str] = Field(default_factory=list, max_length=20)
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("creative_rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _text(value)

    @model_validator(mode="after")
    def selected_idea_must_clear_the_gate(self) -> "CreativeDirection":
        candidate_ids = {item.id for item in self.big_idea_candidates}
        selected_id = self.winning_concept.candidate_id
        if selected_id not in candidate_ids:
            raise ValueError("selected big idea must reference a supplied candidate")
        if self.quality_gate.candidate_id != selected_id:
            raise ValueError("the quality gate must describe the selected big idea")
        rejected_ids = [item.candidate_id for item in self.rejected_concepts]
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("rejected concept IDs must be unique")
        if set(rejected_ids) != candidate_ids - {selected_id}:
            raise ValueError("every non-winning candidate must be recorded as rejected")
        if [item.rank for item in self.rejected_concepts] != list(
            range(2, len(self.big_idea_candidates) + 1)
        ):
            raise ValueError("rejected concepts must preserve deterministic ranking")
        failed = self.quality_gate.failures
        if failed:
            raise ValueError(
                "selected big idea is below the creative quality bar: "
                + ", ".join(
                    f"{check.dimension} {check.score}/{check.threshold}" for check in failed
                )
            )
        return self

    @property
    def selected_big_idea_id(self) -> str:
        """Compatibility accessor; persisted output uses `winning_concept`."""
        return self.winning_concept.candidate_id


def _text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("text cannot be blank")
    return normalized


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = _text(value)
        if normalized not in result:
            result.append(normalized)
    return result


def _require_unique(values: list[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


__all__ = [
    "QUALITY_THRESHOLDS",
    "CONCEPT_SELECTION_DIMENSIONS",
    "BigIdeaCandidate",
    "CreativeAngle",
    "CreativeDirection",
    "CreativeDirectorInput",
    "CreativeDirectorLLMOutput",
    "CreativeEvaluation",
    "CreativeQualityGate",
    "CreativeTerritory",
    "QualityCheck",
    "RejectedConcept",
    "VisualHook",
    "WinningConcept",
]
