from enum import StrEnum


class CampaignStatus(StrEnum):
    BRIEFING = "BRIEFING"
    READY = "READY"
    GENERATING = "GENERATING"
    PLAN_READY = "PLAN_READY"


__all__ = ["CampaignStatus"]
