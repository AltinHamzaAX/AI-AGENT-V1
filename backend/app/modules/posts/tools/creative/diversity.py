import hashlib
import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.modules.posts.agents.art_director import ArtDirection
    from app.modules.posts.agents.copywriter import CopyDraft
    from app.modules.posts.agents.creative_director import CreativeDirection
    from app.modules.posts.agents.design_spec import DesignSpec

CREATIVE_DNA_SCHEMA_VERSION = "1.0"
REPETITION_SIMILARITY_THRESHOLD = 0.82
REPETITION_DIMENSION_THRESHOLD = 3


class CreativeDNADimension(StrEnum):
    LAYOUT = "layout"
    CONCEPT = "concept"
    VISUAL_HOOK = "visual_hook"
    GRAPHIC_SYSTEM = "graphic_system"
    TYPOGRAPHY_PATTERN = "typography_pattern"
    COLOR_BEHAVIOR = "color_behavior"
    COMPOSITION = "composition"
    CTA_TREATMENT = "cta_treatment"
    LOGO_PLACEMENT = "logo_placement"


class CreativeDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = CREATIVE_DNA_SCHEMA_VERSION
    layout: str = Field(min_length=1, max_length=4_000)
    concept: str = Field(min_length=1, max_length=4_000)
    visual_hook: str = Field(min_length=1, max_length=4_000)
    graphic_system: str = Field(min_length=1, max_length=4_000)
    typography_pattern: str = Field(min_length=1, max_length=4_000)
    color_behavior: str = Field(min_length=1, max_length=4_000)
    composition: str = Field(min_length=1, max_length=4_000)
    cta_treatment: str = Field(min_length=1, max_length=4_000)
    logo_placement: str = Field(min_length=1, max_length=4_000)
    fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator(*[item.value for item in CreativeDNADimension])
    @classmethod
    def normalize_dimensions(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("creative DNA dimensions cannot be blank")
        return normalized


class RepetitionMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    historical_fingerprint: str = Field(min_length=64, max_length=64)
    repeated_dimensions: list[CreativeDNADimension] = Field(min_length=3, max_length=9)
    dimension_similarity: dict[CreativeDNADimension, float]
    overall_similarity: float = Field(ge=0, le=1)


def extract_creative_dna(
    *,
    direction: "CreativeDirection",
    copy: "CopyDraft",
    art: "ArtDirection",
    spec: "DesignSpec",
) -> CreativeDNA:
    idea = next(
        item
        for item in direction.big_idea_candidates
        if item.id == direction.winning_concept.candidate_id
    )
    territory = next(
        item for item in direction.creative_territories if item.id == idea.territory_id
    )
    hook = next(item for item in direction.visual_hooks if item.id == idea.visual_hook_id)
    dimensions = {
        "layout": _json(spec.regions.model_dump(mode="json")),
        "concept": " | ".join((territory.premise, idea.idea, idea.territory_link)),
        "visual_hook": " | ".join((hook.description, hook.symbol, hook.mechanism)),
        "graphic_system": art.graphic_language
        + " | "
        + _json([item.model_dump(mode="json") for item in spec.graphic_elements]),
        "typography_pattern": art.typography_direction
        + " | "
        + _json([item.model_dump(mode="json") for item in spec.typography_roles]),
        "color_behavior": art.color_direction
        + " | "
        + _json([item.model_dump(mode="json") for item in spec.color_system]),
        "composition": " | ".join(
            (art.focal_point, art.composition, str(art.product_dominance))
        ),
        "cta_treatment": " | ".join((copy.cta, art.cta_treatment)),
        "logo_placement": art.logo_region
        + " | "
        + _json(spec.regions.logo_region.model_dump(mode="json")),
    }
    fingerprint = hashlib.sha256(_json(dimensions).encode()).hexdigest()
    return CreativeDNA(**dimensions, fingerprint=fingerprint)


def find_repetition(
    current: CreativeDNA,
    history: list[CreativeDNA],
    *,
    similarity_threshold: float = REPETITION_SIMILARITY_THRESHOLD,
    dimension_threshold: int = REPETITION_DIMENSION_THRESHOLD,
) -> list[RepetitionMatch]:
    matches: list[RepetitionMatch] = []
    for previous in history:
        scores = {
            dimension: _similarity(
                getattr(current, dimension.value), getattr(previous, dimension.value)
            )
            for dimension in CreativeDNADimension
        }
        repeated = [
            dimension for dimension, score in scores.items() if score >= similarity_threshold
        ]
        if len(repeated) < dimension_threshold:
            continue
        matches.append(
            RepetitionMatch(
                historical_fingerprint=previous.fingerprint,
                repeated_dimensions=repeated,
                dimension_similarity=scores,
                overall_similarity=round(sum(scores.values()) / len(scores), 4),
            )
        )
    return sorted(matches, key=lambda item: item.overall_similarity, reverse=True)


def _similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[\w#.-]+", left.casefold()))
    right_tokens = set(re.findall(r"[\w#.-]+", right.casefold()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "CREATIVE_DNA_SCHEMA_VERSION",
    "REPETITION_DIMENSION_THRESHOLD",
    "REPETITION_SIMILARITY_THRESHOLD",
    "CreativeDNA",
    "CreativeDNADimension",
    "RepetitionMatch",
    "extract_creative_dna",
    "find_repetition",
]
