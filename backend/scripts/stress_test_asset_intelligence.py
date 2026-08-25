"""Super-hard Ticket 16 test using the configured real LLM and deterministic gates."""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_llm_provider  # noqa: E402
from app.modules.posts.agents.asset_intelligence import (  # noqa: E402
    ASSET_INTELLIGENCE_AGENT_NAME,
    AssetIntelligenceInput,
    AssetPolicy,
    AssetUsageAssertion,
    enforce_asset_usage,
    evaluate_asset_usage,
    register_asset_intelligence_agent,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.domain.semantic_contract import PostSemanticContract  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    message: str
    attachments: tuple[dict[str, Any], ...]
    expected_roles: dict[UUID, str]
    required_assets: tuple[UUID, ...] = ()


def _id(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012d}")


def _attachment(identifier: UUID, role: str, filename: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "declared_role": role,
        "original_filename": filename,
        "mime_type": "image/png",
        "width": 1600,
        "height": 1000,
        "metadata": {"source": "client", "checksum_verified": True},
    }


def _scenarios() -> tuple[Scenario, ...]:
    logo, product = _id(101), _id(102)
    vehicle_primary = _id(201)
    vehicle_reference = _id(301)
    matrix_ids = {
        "brand_logo": _id(401),
        "primary_product": _id(402),
        "packaging": _id(403),
        "environment": _id(404),
        "background_reference": _id(405),
        "style_reference": _id(406),
        "supporting_asset": _id(407),
        "inspiration_only": _id(408),
    }
    return (
        Scenario(
            name="protected_logo_and_product",
            message=(
                "This is our official logo and this is the exact coffee package. "
                "Both files must be used without replacement."
            ),
            attachments=(
                _attachment(logo, "logo", "official-logo.png"),
                _attachment(product, "product", "coffee-package.png"),
            ),
            expected_roles={logo: "brand_logo", product: "primary_product"},
            required_assets=(logo, product),
        ),
        Scenario(
            name="albanian_vehicle_primary_intent",
            message=(
                "Kjo është vetura që duhet të përdoret. Mos e zëvendëso dhe ruaje "
                "pamjen origjinale të produktit."
            ),
            attachments=(_attachment(vehicle_primary, "vehicle", "skoda-fabia.png"),),
            expected_roles={vehicle_primary: "primary_product"},
            required_assets=(vehicle_primary,),
        ),
        Scenario(
            name="vehicle_without_primary_intent",
            message="This vehicle is a reference attachment; no primary-product claim is made.",
            attachments=(_attachment(vehicle_reference, "vehicle", "vehicle-reference.png"),),
            expected_roles={vehicle_reference: "vehicle"},
        ),
        Scenario(
            name="complete_role_matrix",
            message=(
                "Every attachment is labelled with its declared purpose. Keep those declared "
                "roles and do not reinterpret them."
            ),
            attachments=(
                _attachment(matrix_ids["brand_logo"], "logo", "logo.png"),
                _attachment(matrix_ids["primary_product"], "product", "product.png"),
                _attachment(matrix_ids["packaging"], "packaging", "packaging.png"),
                _attachment(matrix_ids["environment"], "environment", "venue.png"),
                _attachment(matrix_ids["background_reference"], "background", "background.png"),
                _attachment(matrix_ids["style_reference"], "style_reference", "style.png"),
                _attachment(matrix_ids["supporting_asset"], "supporting_asset", "support.png"),
                _attachment(matrix_ids["inspiration_only"], "inspiration", "mood.png"),
            ),
            expected_roles={identifier: role for role, identifier in matrix_ids.items()},
            required_assets=(
                matrix_ids["brand_logo"],
                matrix_ids["primary_product"],
            ),
        ),
    )


def _contract(scenario: Scenario) -> PostSemanticContract:
    return PostSemanticContract.create(
        company="Promotiva Stress Lab",
        brand="VERIFIED BRAND",
        product="VERIFIED PRODUCT",
        primary_entity="VERIFIED PRODUCT",
        goal="Validate asset fidelity",
        audience="Quality-assurance reviewers",
        market="Kosovo",
        location="Prishtina",
        offer=None,
        cta_intent="Review now",
        platform="Instagram",
        language="Albanian",
        required_facts={"identity rule": "Original client assets must remain unchanged"},
        forbidden_claims=["replacement is equivalent to the original"],
        required_assets=list(scenario.required_assets),
        constraints=["Never replace the logo or promoted product"],
    )


def _input(scenario: Scenario) -> AssetIntelligenceInput:
    return AssetIntelligenceInput(
        semantic_contract=_contract(scenario).to_dict(),
        latest_message=scenario.message,
        conversation_history=[
            "assistant: Upload the exact assets and identify their intended use."
        ],
        attachments=list(scenario.attachments),
    )


_EXPECTED_POLICY = {
    "brand_logo": (True, False, False, 0.03, 0.20),
    "primary_product": (True, False, False, 0.30, 0.85),
    "vehicle": (True, False, False, 0.25, 0.85),
    "packaging": (True, False, False, 0.20, 0.80),
    "environment": (False, True, True, 0.10, 1.00),
    "background_reference": (False, True, True, 0.00, 1.00),
    "style_reference": (False, True, True, 0.00, 0.00),
    "supporting_asset": (False, True, False, 0.00, 0.45),
    "inspiration_only": (False, True, True, 0.00, 0.00),
}


def _verify_result(
    scenario: Scenario,
    assets: list[AssetPolicy],
    fingerprint: str,
) -> None:
    by_id = {asset.asset_id: asset for asset in assets}
    _require(len(by_id) == len(assets), "duplicate asset IDs returned")
    _require(set(by_id) == set(scenario.expected_roles), "asset IDs were added or omitted")
    _require(fingerprint == _contract(scenario).fingerprint, "contract fingerprint drifted")

    for identifier, expected_role in scenario.expected_roles.items():
        policy = by_id[identifier]
        _require(policy.role.value == expected_role, f"{identifier}: unexpected role")
        _require(policy.contract_fingerprint == fingerprint, f"{identifier}: bad fingerprint")
        preserve, allow_replace, allow_generation, minimum, maximum = _EXPECTED_POLICY[
            expected_role
        ]
        _require(policy.preserve_identity is preserve, f"{identifier}: preserve policy drift")
        _require(policy.allow_replace is allow_replace, f"{identifier}: replace policy drift")
        _require(
            policy.allow_generation is allow_generation,
            f"{identifier}: generation policy drift",
        )
        _require(policy.min_dominance == minimum, f"{identifier}: min dominance drift")
        _require(policy.max_dominance == maximum, f"{identifier}: max dominance drift")
        expected_required = identifier in scenario.required_assets or expected_role in {
            "brand_logo",
            "primary_product",
        }
        _require(policy.required is expected_required, f"{identifier}: required policy drift")

    _verify_valid_usage(assets)


def _verify_valid_usage(assets: list[AssetPolicy]) -> None:
    assertions = []
    for policy in assets:
        dominance = (policy.min_dominance + policy.max_dominance) / 2
        assertions.append(
            AssetUsageAssertion(
                asset_id=policy.asset_id,
                used=True,
                identity_preserved=True if policy.preserve_identity else False,
                dominance=dominance,
            )
        )
    validation = enforce_asset_usage(assets, assertions)
    _require(validation.decision == "CONTINUE", "valid usage did not continue")


def _verify_hard_fail_matrix(all_assets: list[AssetPolicy]) -> None:
    protected = next(asset for asset in all_assets if asset.role.value == "primary_product")
    logo = next(asset for asset in all_assets if asset.role.value == "brand_logo")
    background = next(asset for asset in all_assets if asset.role.value == "background_reference")

    failures = (
        AssetUsageAssertion(
            asset_id=protected.asset_id,
            used=True,
            identity_preserved=False,
            replaced_by=_id(999001),
            dominance=0.5,
        ),
        AssetUsageAssertion(
            asset_id=protected.asset_id,
            used=True,
            identity_preserved=False,
            generated_substitute=True,
            dominance=0.5,
        ),
        AssetUsageAssertion(asset_id=protected.asset_id, used=False),
        AssetUsageAssertion(
            asset_id=logo.asset_id,
            used=True,
            identity_preserved=True,
            cropped=True,
            dominance=0.1,
        ),
        AssetUsageAssertion(
            asset_id=logo.asset_id,
            used=True,
            identity_preserved=True,
            dominance=0.95,
        ),
    )
    for index, assertion in enumerate(failures, start=1):
        policy = protected if assertion.asset_id == protected.asset_id else logo
        result = evaluate_asset_usage([policy], [assertion])
        _require(not result.valid, f"hard-fail case {index} unexpectedly passed")
        _require(result.decision == "HARD_FAIL", f"hard-fail case {index} wrong decision")
        _require(bool(result.violations), f"hard-fail case {index} has no violations")

    replaceable = enforce_asset_usage(
        [background],
        [
            AssetUsageAssertion(
                asset_id=background.asset_id,
                used=True,
                replaced_by=_id(999002),
                generated_substitute=True,
                dominance=1,
            )
        ],
    )
    _require(replaceable.decision == "CONTINUE", "replaceable background was blocked")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _run(rounds: int) -> None:
    settings = get_settings()
    if (
        not Path("/.dockerenv").exists()
        and settings.ollama_base_url.rstrip("/") == "http://host.docker.internal:11434"
    ):
        settings = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})

    runtime = AgentRuntime(ToolRegistry())
    register_asset_intelligence_agent(runtime, create_llm_provider(settings))
    fingerprints: dict[str, str] = {}
    collected: list[AssetPolicy] = []
    scenarios = _scenarios()

    for round_number in range(1, rounds + 1):
        print(f"\n========== SUPER-HARD ROUND {round_number}/{rounds} ==========")
        for scenario in scenarios:
            print(f"[RUN] {scenario.name}")
            result = await runtime.run(ASSET_INTELLIGENCE_AGENT_NAME, _input(scenario))
            _verify_result(scenario, result.assets, result.contract_fingerprint)
            previous = fingerprints.setdefault(scenario.name, result.contract_fingerprint)
            _require(previous == result.contract_fingerprint, "fingerprint changed between rounds")
            collected.extend(result.assets)
            print(f"[PASS] {scenario.name}: {len(result.assets)} assets verified")

    print("\n[RUN] deterministic HARD_FAIL matrix")
    _verify_hard_fail_matrix(collected)
    print("[PASS] replacement, generation, identity, crop, missing, and dominance gates")
    print(
        f"\nALL SUPER-HARD CHECKS PASSED: {rounds * len(scenarios)} real provider runs, "
        f"{sum(len(scenario.attachments) for scenario in scenarios) * rounds} classifications"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        choices=range(1, 11),
        metavar="1-10",
        help="Number of complete real-provider rounds (default: 2).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    asyncio.run(_run(arguments.rounds))
