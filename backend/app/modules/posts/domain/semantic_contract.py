import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

SEMANTIC_CONTRACT_VERSION = 1
PROTECTED_SCALAR_FIELDS = (
    "company",
    "brand",
    "product",
    "primary_entity",
    "goal",
    "audience",
    "market",
    "location",
    "offer",
    "cta_intent",
    "platform",
    "language",
)


def _clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _semantic(value: str) -> str:
    return _clean(value).casefold()


def _required_text(name: str, value: str, *, limit: int = 500) -> str:
    cleaned = _clean(value)
    if not cleaned:
        raise ValueError(f"{name} cannot be blank")
    if len(cleaned) > limit:
        raise ValueError(f"{name} cannot exceed {limit} characters")
    return cleaned


def _optional_text(name: str, value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    cleaned = _clean(value)
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ValueError(f"{name} cannot exceed {limit} characters")
    return cleaned


def _string_list(name: str, values: list[str], *, limit: int = 100) -> tuple[str, ...]:
    if len(values) > limit:
        raise ValueError(f"{name} cannot contain more than {limit} values")
    normalized = {_required_text(name, value) for value in values}
    return tuple(sorted(normalized, key=_semantic))


@dataclass(frozen=True, slots=True)
class PostSemanticContract:
    company: str | None
    brand: str | None
    product: str | None
    primary_entity: str
    goal: str
    audience: str
    market: str | None
    location: str | None
    offer: str | None
    cta_intent: str
    platform: str
    language: str
    required_facts: tuple[tuple[str, str], ...] = field(repr=False)
    forbidden_claims: tuple[str, ...] = field(repr=False)
    required_assets: tuple[UUID, ...] = field(repr=False)
    constraints: tuple[str, ...] = field(repr=False)
    fingerprint: str
    contract_version: int = SEMANTIC_CONTRACT_VERSION

    @classmethod
    def create(
        cls,
        *,
        company: str | None,
        brand: str | None,
        product: str | None,
        primary_entity: str,
        goal: str,
        audience: str,
        market: str | None,
        location: str | None,
        offer: str | None,
        cta_intent: str,
        platform: str,
        language: str,
        required_facts: dict[str, str],
        forbidden_claims: list[str],
        required_assets: list[UUID],
        constraints: list[str],
    ) -> "PostSemanticContract":
        if len(required_facts) > 100:
            raise ValueError("required_facts cannot contain more than 100 values")
        normalized_facts: dict[str, tuple[str, str]] = {}
        for key, value in required_facts.items():
            clean_key = _required_text("required fact name", key, limit=100)
            semantic_key = _semantic(clean_key)
            if semantic_key in normalized_facts:
                raise ValueError(f"required_facts contains duplicate key: {clean_key}")
            normalized_facts[semantic_key] = (
                clean_key,
                _required_text("required fact value", value),
            )
        facts = tuple(sorted(normalized_facts.values(), key=lambda item: _semantic(item[0])))
        assets = tuple(sorted(set(required_assets), key=str))
        if len(assets) > 100:
            raise ValueError("required_assets cannot contain more than 100 values")
        values: dict[str, Any] = {
            "contract_version": SEMANTIC_CONTRACT_VERSION,
            "company": _optional_text("company", company),
            "brand": _optional_text("brand", brand),
            "product": _optional_text("product", product),
            "primary_entity": _required_text("primary_entity", primary_entity),
            "goal": _required_text("goal", goal),
            "audience": _required_text("audience", audience),
            "market": _optional_text("market", market),
            "location": _optional_text("location", location),
            "offer": _optional_text("offer", offer),
            "cta_intent": _required_text("cta_intent", cta_intent),
            "platform": _required_text("platform", platform, limit=100),
            "language": _required_text("language", language, limit=100),
            "required_facts": dict(facts),
            "forbidden_claims": list(_string_list("forbidden_claims", forbidden_claims)),
            "required_assets": [str(asset_id) for asset_id in assets],
            "constraints": list(_string_list("constraints", constraints)),
        }
        fingerprint = _fingerprint(values)
        return cls(
            company=values["company"],
            brand=values["brand"],
            product=values["product"],
            primary_entity=values["primary_entity"],
            goal=values["goal"],
            audience=values["audience"],
            market=values["market"],
            location=values["location"],
            offer=values["offer"],
            cta_intent=values["cta_intent"],
            platform=values["platform"],
            language=values["language"],
            required_facts=facts,
            forbidden_claims=tuple(values["forbidden_claims"]),
            required_assets=assets,
            constraints=tuple(values["constraints"]),
            fingerprint=fingerprint,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PostSemanticContract":
        expected_fields = {
            *PROTECTED_SCALAR_FIELDS,
            "required_facts",
            "forbidden_claims",
            "required_assets",
            "constraints",
            "fingerprint",
            "contract_version",
        }
        if set(value) != expected_fields:
            raise ValueError("Stored semantic contract has an invalid shape")
        if value["contract_version"] != SEMANTIC_CONTRACT_VERSION:
            raise ValueError("Stored semantic contract version is not supported")
        contract = cls.create(
            company=value["company"],
            brand=value["brand"],
            product=value["product"],
            primary_entity=value["primary_entity"],
            goal=value["goal"],
            audience=value["audience"],
            market=value["market"],
            location=value["location"],
            offer=value["offer"],
            cta_intent=value["cta_intent"],
            platform=value["platform"],
            language=value["language"],
            required_facts=dict(value["required_facts"]),
            forbidden_claims=list(value["forbidden_claims"]),
            required_assets=[UUID(asset_id) for asset_id in value["required_assets"]],
            constraints=list(value["constraints"]),
        )
        if value["fingerprint"] != contract.fingerprint:
            raise ValueError("Stored semantic contract fingerprint is invalid")
        return contract

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "company": self.company,
            "brand": self.brand,
            "product": self.product,
            "primary_entity": self.primary_entity,
            "goal": self.goal,
            "audience": self.audience,
            "market": self.market,
            "location": self.location,
            "offer": self.offer,
            "cta_intent": self.cta_intent,
            "platform": self.platform,
            "language": self.language,
            "required_facts": dict(self.required_facts),
            "forbidden_claims": list(self.forbidden_claims),
            "required_assets": [str(asset_id) for asset_id in self.required_assets],
            "constraints": list(self.constraints),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class SemanticAssertions:
    contract_fingerprint: str | None = None
    protected_values: dict[str, str | None] = field(default_factory=dict)
    required_facts: dict[str, str] = field(default_factory=dict)
    claims: tuple[str, ...] = ()
    used_assets: tuple[UUID, ...] | None = None


def semantic_contract_violations(
    contract: PostSemanticContract,
    assertions: SemanticAssertions,
) -> tuple[str, ...]:
    violations: list[str] = []
    if (
        assertions.contract_fingerprint is not None
        and assertions.contract_fingerprint != contract.fingerprint
    ):
        violations.append("semantic contract fingerprint does not match")
    for field_name, actual in assertions.protected_values.items():
        if field_name not in PROTECTED_SCALAR_FIELDS:
            raise ValueError(f"Unsupported protected semantic field: {field_name}")
        expected = getattr(contract, field_name)
        if (expected is None) != (actual is None) or (
            expected is not None and actual is not None and _semantic(expected) != _semantic(actual)
        ):
            violations.append(f"{field_name} changed from {expected!r} to {actual!r}")

    expected_facts = {
        _semantic(fact_name): (fact_name, fact_value)
        for fact_name, fact_value in contract.required_facts
    }
    for fact_name, actual in assertions.required_facts.items():
        expected_entry = expected_facts.get(_semantic(fact_name))
        if expected_entry is not None and _semantic(expected_entry[1]) != _semantic(actual):
            violations.append(
                f"required fact {expected_entry[0]!r} changed from "
                f"{expected_entry[1]!r} to {actual!r}"
            )

    normalized_claims = [(_semantic(claim), claim) for claim in assertions.claims]
    for forbidden in contract.forbidden_claims:
        forbidden_normalized = _semantic(forbidden)
        for normalized_claim, original_claim in normalized_claims:
            if forbidden_normalized in normalized_claim:
                violations.append(f"forbidden claim detected: {original_claim!r}")

    if assertions.used_assets is not None:
        missing = set(contract.required_assets) - set(assertions.used_assets)
        violations.extend(
            f"required asset missing: {asset_id}" for asset_id in sorted(missing, key=str)
        )
    return tuple(violations)


def _fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "PROTECTED_SCALAR_FIELDS",
    "SEMANTIC_CONTRACT_VERSION",
    "PostSemanticContract",
    "SemanticAssertions",
    "semantic_contract_violations",
]
