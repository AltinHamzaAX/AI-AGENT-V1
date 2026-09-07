import unicodedata
from decimal import Decimal

from app.modules.campaigns.domain.exceptions import CampaignPlanValidationError
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan


class CampaignPlanValidator:
    """Validate deterministic Brief-to-Plan invariants without semantic guessing."""

    def validate(self, brief: CampaignBrief, plan: CampaignPlan) -> CampaignPlan:
        issues: list[str] = []
        self._validate_location(brief, plan, issues)
        self._validate_budget(brief, plan, issues)
        self._validate_channels(brief, plan, issues)
        if issues:
            raise CampaignPlanValidationError(tuple(issues))
        return plan

    @staticmethod
    def _validate_location(
        brief: CampaignBrief,
        plan: CampaignPlan,
        issues: list[str],
    ) -> None:
        if brief.location and not _location_matches(
            brief.location,
            plan.target_audience.location,
        ):
            issues.append("location.mismatch")

    @staticmethod
    def _validate_budget(
        brief: CampaignBrief,
        plan: CampaignPlan,
        issues: list[str],
    ) -> None:
        allocation = plan.budget_allocation
        if brief.budget_amount is not None:
            if allocation is None:
                issues.append("budget.missing")
                return
            if allocation.total != brief.budget_amount:
                issues.append("budget.total_mismatch")
            if brief.budget_currency and _normalized(brief.budget_currency) != _normalized(
                allocation.currency
            ):
                issues.append("budget.currency_mismatch")
        if allocation is not None:
            item_total = sum((item.amount for item in allocation.items), start=Decimal(0))
            if item_total != allocation.total:
                issues.append("budget.items_total_mismatch")

    @staticmethod
    def _validate_channels(
        brief: CampaignBrief,
        plan: CampaignPlan,
        issues: list[str],
    ) -> None:
        if not brief.channels:
            return
        plan_channels = {_normalized(channel.name) for channel in plan.channels}
        if any(_normalized(channel) not in plan_channels for channel in brief.channels):
            issues.append("channels.confirmed_missing")


def _location_matches(confirmed: str, actual: str | None) -> bool:
    if actual is None:
        return False
    confirmed_value = _normalized(confirmed)
    actual_value = _normalized(actual)
    return confirmed_value in actual_value or actual_value in confirmed_value


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    return " ".join(characters.split())


__all__ = ["CampaignPlanValidator"]
