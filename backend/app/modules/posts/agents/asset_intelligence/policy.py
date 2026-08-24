from collections.abc import Iterable
from uuid import UUID

from .schemas import (
    AssetPolicy,
    AssetPolicyValidation,
    AssetUsageAssertion,
)


class AssetPolicyHardFail(ValueError):
    code = "ASSET_POLICY_HARD_FAIL"

    def __init__(self, violations: Iterable[str]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


def evaluate_asset_usage(
    policies: Iterable[AssetPolicy],
    assertions: Iterable[AssetUsageAssertion],
) -> AssetPolicyValidation:
    policy_by_id = _unique_by_id(policies, "asset policies")
    assertion_by_id = _unique_by_id(assertions, "asset assertions")
    unknown = set(assertion_by_id) - set(policy_by_id)
    if unknown:
        identifiers = ", ".join(sorted(str(identifier) for identifier in unknown))
        raise ValueError(f"asset assertions contain unknown IDs: {identifiers}")

    violations: list[str] = []
    for asset_id, policy in policy_by_id.items():
        assertion = assertion_by_id.get(asset_id)
        if assertion is None:
            if policy.required:
                violations.append(f"required asset '{asset_id}' has no usage assertion")
            continue
        if policy.required and not assertion.used:
            violations.append(f"required asset '{asset_id}' is missing from the composition")
        if assertion.replaced_by is not None and not policy.allow_replace:
            violations.append(f"asset '{asset_id}' cannot be replaced")
        if assertion.generated_substitute and not policy.allow_generation:
            violations.append(f"asset '{asset_id}' cannot be substituted with generated content")
        if assertion.used and policy.preserve_identity and assertion.identity_preserved is not True:
            violations.append(f"asset '{asset_id}' identity was not preserved")
        if assertion.cropped and not policy.allow_crop:
            violations.append(f"asset '{asset_id}' cannot be cropped")
        if assertion.used and assertion.dominance is None:
            violations.append(f"asset '{asset_id}' is missing a dominance measurement")
        if assertion.used and assertion.dominance is not None and not (
            policy.min_dominance <= assertion.dominance <= policy.max_dominance
        ):
            violations.append(
                f"asset '{asset_id}' dominance must be between "
                f"{policy.min_dominance:g} and {policy.max_dominance:g}"
            )

    return AssetPolicyValidation(
        valid=not violations,
        decision="CONTINUE" if not violations else "HARD_FAIL",
        violations=violations,
    )


def enforce_asset_usage(
    policies: Iterable[AssetPolicy],
    assertions: Iterable[AssetUsageAssertion],
) -> AssetPolicyValidation:
    result = evaluate_asset_usage(policies, assertions)
    if not result.valid:
        raise AssetPolicyHardFail(result.violations)
    return result


def _unique_by_id[T: AssetPolicy | AssetUsageAssertion](
    values: Iterable[T], name: str
) -> dict[UUID, T]:
    result: dict[UUID, T] = {}
    for value in values:
        if value.asset_id in result:
            raise ValueError(f"{name} must have unique asset IDs")
        result[value.asset_id] = value
    return result


__all__ = ["AssetPolicyHardFail", "enforce_asset_usage", "evaluate_asset_usage"]
