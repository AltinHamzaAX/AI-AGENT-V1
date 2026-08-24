from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrandProductInput(BaseModel):
    """Immutable facts available to the Brand & Product specialist."""

    model_config = ConfigDict(extra="forbid")

    semantic_contract: dict[str, Any]


class FeatureBenefitValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fact: str = Field(min_length=1, max_length=100)
    feature: str = Field(min_length=1, max_length=500)
    benefit: str = Field(min_length=1, max_length=500)
    customer_value: str = Field(min_length=1, max_length=500)


class USPCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    source_facts: list[str] = Field(min_length=1, max_length=20)


class BrandAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=500)
    identity_summary: str = Field(min_length=1, max_length=1_000)
    personality_traits: list[str] = Field(default_factory=list, max_length=20)
    verified_facts: dict[str, str] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class ProductAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=500)
    primary_entity: str = Field(min_length=1, max_length=500)
    offer: str | None = Field(default=None, max_length=500)
    feature_benefit_value: list[FeatureBenefitValue] = Field(
        default_factory=list,
        max_length=100,
    )
    usp_candidates: list[USPCandidate] = Field(default_factory=list, max_length=50)
    verified_facts: dict[str, str] = Field(default_factory=dict)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    required_assets: list[UUID] = Field(default_factory=list, max_length=100)
    contract_fingerprint: str = Field(min_length=64, max_length=64)


class BrandProductAnalysis(BaseModel):
    """Factual analysis only; downstream strategy and copy are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    brand: BrandAnalysis
    product: ProductAnalysis


class BrandProductLLMOutput(BaseModel):
    """Reasoned fields supplied by the provider; protected facts are deterministic."""

    model_config = ConfigDict(extra="forbid")

    identity_summary: str = Field(min_length=1, max_length=1_000)
    personality_traits: list[str] = Field(default_factory=list, max_length=20)
    brand_fact_keys: list[str] = Field(default_factory=list, max_length=100)
    product_fact_keys: list[str] = Field(default_factory=list, max_length=100)
    feature_benefit_value: list[FeatureBenefitValue] = Field(
        default_factory=list,
        max_length=100,
    )
    usp_candidates: list[USPCandidate] = Field(default_factory=list, max_length=50)

    @field_validator("personality_traits", "brand_fact_keys", "product_fact_keys")
    @classmethod
    def normalize_unique_strings(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = " ".join(value.split())
            if not normalized:
                raise ValueError("list values cannot be blank")
            if normalized not in result:
                result.append(normalized)
        return result


__all__ = [
    "BrandAnalysis",
    "BrandProductAnalysis",
    "BrandProductInput",
    "BrandProductLLMOutput",
    "FeatureBenefitValue",
    "ProductAnalysis",
    "USPCandidate",
]
