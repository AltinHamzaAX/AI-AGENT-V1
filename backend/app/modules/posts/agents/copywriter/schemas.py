from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.posts.agents.brand_product import BrandAnalysis
from app.modules.posts.agents.creative_director import CreativeDirection
from app.modules.posts.agents.marketing_strategist import MarketingStrategy
from app.modules.posts.domain.semantic_contract import PostSemanticContract


class CopywriterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: MarketingStrategy
    concept: CreativeDirection
    brand_voice: BrandAnalysis
    platform: str = Field(min_length=1, max_length=100)
    offer: str | None = Field(default=None, max_length=500)
    semantic_contract: dict[str, Any]

    @model_validator(mode="after")
    def inputs_must_describe_one_post(self) -> "CopywriterInput":
        try:
            contract = PostSemanticContract.from_dict(self.semantic_contract)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("copywriter requires a valid semantic contract") from exc
        fingerprints = {
            contract.fingerprint,
            self.strategy.contract_fingerprint,
            self.concept.contract_fingerprint,
            self.brand_voice.contract_fingerprint,
        }
        if len(fingerprints) != 1:
            raise ValueError("copywriter inputs disagree on the semantic contract")
        if _semantic(self.platform) != _semantic(contract.platform):
            raise ValueError("copywriter platform disagrees with the semantic contract")
        if _optional_semantic(self.offer) != _optional_semantic(contract.offer):
            raise ValueError("copywriter offer disagrees with the semantic contract")
        return self


class CopywriterLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=80)
    subheadline: str = Field(min_length=1, max_length=140)
    supporting_copy: str = Field(min_length=1, max_length=240)
    offer_copy: str | None = Field(default=None, max_length=160)
    cta: str = Field(min_length=1, max_length=40)
    caption: str = Field(min_length=1, max_length=2_200)
    hashtags: list[str] = Field(default_factory=list, max_length=15)

    @field_validator(
        "headline",
        "subheadline",
        "supporting_copy",
        "offer_copy",
        "cta",
        "caption",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("copy fields cannot be blank")
        return normalized

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            tag = value.strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag}"
            if len(tag) > 50 or any(character.isspace() for character in tag):
                raise ValueError("hashtags must be single tokens of at most 50 characters")
            if tag.casefold() not in {item.casefold() for item in result}:
                result.append(tag)
        return result


class CopyQualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    passed: bool
    detail: str


class CopyQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CopyQualityCheck] = Field(min_length=7, max_length=7)

    @property
    def failures(self) -> list[CopyQualityCheck]:
        return [check for check in self.checks if not check.passed]


class CopyDraft(CopywriterLLMOutput):
    model_config = ConfigDict(extra="forbid")

    quality: CopyQuality
    contract_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def quality_must_pass(self) -> "CopyDraft":
        if self.quality.failures:
            raise ValueError("copy draft contains failed quality checks")
        return self


def _semantic(value: str) -> str:
    return " ".join(value.casefold().split())


def _optional_semantic(value: str | None) -> str | None:
    return _semantic(value) if value is not None else None


__all__ = [
    "CopyDraft",
    "CopyQuality",
    "CopyQualityCheck",
    "CopywriterInput",
    "CopywriterLLMOutput",
]
