from dataclasses import dataclass
from enum import StrEnum

from app.modules.campaigns.domain.enums import CampaignStatus
from app.modules.campaigns.domain.exceptions import InvalidCampaignTransitionError
from app.modules.campaigns.schemas.models import CampaignBrief


BUSINESS_REQUIREMENT = "business_or_product_service"


@dataclass(frozen=True, slots=True)
class CampaignReadiness:
    ready: bool
    missing_fields: tuple[str, ...]


class CampaignEvent(StrEnum):
    GENERATION_REQUESTED = "GENERATION_REQUESTED"
    PLAN_PERSISTED = "PLAN_PERSISTED"
    GENERATION_FAILED = "GENERATION_FAILED"
    EXPORTED = "EXPORTED"


_EVENT_TRANSITIONS = {
    (CampaignStatus.READY, CampaignEvent.GENERATION_REQUESTED): CampaignStatus.GENERATING,
    (CampaignStatus.GENERATING, CampaignEvent.PLAN_PERSISTED): CampaignStatus.PLAN_READY,
    (CampaignStatus.GENERATING, CampaignEvent.GENERATION_FAILED): CampaignStatus.READY,
    (CampaignStatus.PLAN_READY, CampaignEvent.EXPORTED): CampaignStatus.PLAN_READY,
}


def evaluate_campaign_readiness(brief: CampaignBrief) -> CampaignReadiness:
    missing: list[str] = []
    if brief.business is None and brief.product_or_service is None:
        missing.append(BUSINESS_REQUIREMENT)
    if brief.goal is None:
        missing.append("goal")
    if brief.audience is None:
        missing.append("audience")
    return CampaignReadiness(ready=not missing, missing_fields=tuple(missing))


def status_for_readiness(readiness: CampaignReadiness) -> CampaignStatus:
    return CampaignStatus.READY if readiness.ready else CampaignStatus.BRIEFING


def transition_campaign_status(
    current: CampaignStatus,
    event: CampaignEvent,
) -> CampaignStatus:
    target = _EVENT_TRANSITIONS.get((current, event))
    if target is None:
        raise InvalidCampaignTransitionError(
            f"Campaign cannot handle {event.value} while {current.value}"
        )
    return target


__all__ = [
    "BUSINESS_REQUIREMENT",
    "CampaignEvent",
    "CampaignReadiness",
    "evaluate_campaign_readiness",
    "status_for_readiness",
    "transition_campaign_status",
]
