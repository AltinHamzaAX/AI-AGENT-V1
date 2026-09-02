from dataclasses import dataclass
from uuid import UUID

from app.integrations.llm import ProviderResponseError
from app.modules.campaigns.domain import (
    Campaign,
    CampaignEvent,
    CampaignNotFoundError,
    CampaignPlanValidationError,
    CampaignReadiness,
    CampaignSourceNotFoundError,
    CampaignStatus,
    InvalidCampaignTransitionError,
    evaluate_campaign_readiness,
    status_for_readiness,
    transition_campaign_status,
)
from app.modules.campaigns.repositories import CampaignRepository
from app.modules.campaigns.schemas import CampaignBrief, CampaignPlan
from app.modules.campaigns.services.generation import CampaignPlanGenerator
from app.modules.campaigns.services.validation import CampaignPlanValidator
from app.shared.conversations.domain import ConversationScope


@dataclass(frozen=True, slots=True)
class CampaignBriefUpdateResult:
    brief: CampaignBrief
    changed: bool
    changed_fields: tuple[str, ...]
    plan_exists: bool


@dataclass(frozen=True, slots=True)
class CampaignReadinessStateResult:
    campaign: Campaign
    readiness: CampaignReadiness
    status_changed: bool


@dataclass(frozen=True, slots=True)
class CampaignBriefStateResult:
    update: CampaignBriefUpdateResult
    campaign: Campaign
    readiness: CampaignReadiness
    status_changed: bool


class CampaignService:
    MAX_PLAN_GENERATION_ATTEMPTS = 2

    def __init__(
        self,
        repository: CampaignRepository,
        plan_validator: CampaignPlanValidator | None = None,
    ) -> None:
        self._repository = repository
        self._plan_validator = plan_validator or CampaignPlanValidator()

    async def create_campaign(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
        initial_brief: CampaignBrief | None = None,
    ) -> Campaign:
        if not await self._repository.conversation_exists(
            conversation_id=conversation_id,
            scope=scope,
        ):
            raise CampaignSourceNotFoundError
        brief = initial_brief or CampaignBrief()
        campaign = await self._repository.create_campaign(
            conversation_id=conversation_id,
            initial_brief=brief,
        )
        state = await self._reevaluate_readiness(
            campaign_id=campaign.id,
            scope=scope,
            brief=brief,
            brief_changed=False,
        )
        return state.campaign

    async def get_campaign(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> Campaign:
        campaign = await self._repository.get_campaign(
            campaign_id=campaign_id,
            scope=scope,
        )
        if campaign is None:
            raise CampaignNotFoundError
        return campaign

    async def find_campaign_by_conversation(
        self,
        *,
        conversation_id: UUID,
        scope: ConversationScope,
    ) -> Campaign | None:
        return await self._repository.find_campaign_by_conversation(
            conversation_id=conversation_id,
            scope=scope,
        )

    async def get_brief(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignBrief:
        brief = await self._repository.get_brief(
            campaign_id=campaign_id,
            scope=scope,
        )
        if brief is None:
            raise CampaignNotFoundError
        return brief

    async def get_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
    ) -> CampaignPlan | None:
        campaign = await self.get_campaign(campaign_id=campaign_id, scope=scope)
        if campaign.status is not CampaignStatus.PLAN_READY:
            return None
        return await self._repository.get_plan(
            campaign_id=campaign_id,
            scope=scope,
        )

    async def generate_plan(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        generator: CampaignPlanGenerator,
    ) -> CampaignPlan:
        brief = await self.get_brief(campaign_id=campaign_id, scope=scope)
        await self.transition(
            campaign_id=campaign_id,
            scope=scope,
            event=CampaignEvent.GENERATION_REQUESTED,
        )
        try:
            repair_issues: tuple[str, ...] = ()
            for attempt in range(self.MAX_PLAN_GENERATION_ATTEMPTS):
                try:
                    candidate = await generator.generate(
                        brief,
                        repair_issues=repair_issues,
                    )
                    plan = self._plan_validator.validate(brief, candidate)
                except (ProviderResponseError, CampaignPlanValidationError) as exc:
                    if attempt + 1 == self.MAX_PLAN_GENERATION_ATTEMPTS:
                        raise
                    repair_issues = _repair_issues(exc)
                    continue

                saved = await self._repository.save_plan(
                    campaign_id=campaign_id,
                    scope=scope,
                    plan=plan,
                )
                if saved is None:
                    raise CampaignNotFoundError
                await self.transition(
                    campaign_id=campaign_id,
                    scope=scope,
                    event=CampaignEvent.PLAN_PERSISTED,
                )
                return saved
            raise RuntimeError("Campaign Plan generation attempts exhausted")
        except Exception as failure:
            try:
                await self.transition(
                    campaign_id=campaign_id,
                    scope=scope,
                    event=CampaignEvent.GENERATION_FAILED,
                )
            except Exception as recovery_error:
                # A failed request transaction rolls all generation writes back.
                failure.add_note(
                    "Campaign generation recovery failed with "
                    f"{type(recovery_error).__name__}; caller must roll back the transaction"
                )
            raise

    async def update_brief_from_extraction(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        extracted_fields: CampaignBrief,
    ) -> CampaignBriefUpdateResult:
        current = await self._repository.get_brief(
            campaign_id=campaign_id,
            scope=scope,
        )
        if current is None:
            raise CampaignNotFoundError

        changes = _brief_changes(current, extracted_fields)
        if changes:
            # Validate the complete merged contract before sending a partial patch.
            CampaignBrief.model_validate({**current.model_dump(), **changes})
            updated = await self._repository.update_brief(
                campaign_id=campaign_id,
                scope=scope,
                updates=CampaignBrief.model_validate(changes),
            )
            if updated is None:
                raise CampaignNotFoundError
        else:
            updated = current

        plan = await self._repository.get_plan(
            campaign_id=campaign_id,
            scope=scope,
        )
        return CampaignBriefUpdateResult(
            brief=updated,
            changed=bool(changes),
            changed_fields=tuple(changes),
            plan_exists=plan is not None,
        )

    async def update_brief_and_reevaluate(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        extracted_fields: CampaignBrief,
    ) -> CampaignBriefStateResult:
        campaign = await self.get_campaign(campaign_id=campaign_id, scope=scope)
        if campaign.status is CampaignStatus.GENERATING:
            current = await self.get_brief(campaign_id=campaign_id, scope=scope)
            if _brief_changes(current, extracted_fields):
                raise InvalidCampaignTransitionError(
                    "Campaign Brief cannot change while generation is in progress"
                )
        update = await self.update_brief_from_extraction(
            campaign_id=campaign_id,
            scope=scope,
            extracted_fields=extracted_fields,
        )
        state = await self._reevaluate_readiness(
            campaign_id=campaign_id,
            scope=scope,
            brief=update.brief,
            brief_changed=update.changed,
        )
        return CampaignBriefStateResult(
            update=update,
            campaign=state.campaign,
            readiness=state.readiness,
            status_changed=state.status_changed,
        )

    async def reevaluate_readiness(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        brief_changed: bool = False,
    ) -> CampaignReadinessStateResult:
        brief = await self.get_brief(campaign_id=campaign_id, scope=scope)
        return await self._reevaluate_readiness(
            campaign_id=campaign_id,
            scope=scope,
            brief=brief,
            brief_changed=brief_changed,
        )

    async def transition(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        event: CampaignEvent,
    ) -> Campaign:
        campaign = await self.get_campaign(campaign_id=campaign_id, scope=scope)
        target = transition_campaign_status(campaign.status, event)
        if target is campaign.status:
            return campaign
        updated = await self._repository.update_status(
            campaign_id=campaign_id,
            scope=scope,
            status=target,
        )
        if updated is None:
            raise CampaignNotFoundError
        return updated

    async def _reevaluate_readiness(
        self,
        *,
        campaign_id: UUID,
        scope: ConversationScope,
        brief: CampaignBrief,
        brief_changed: bool,
    ) -> CampaignReadinessStateResult:
        campaign = await self.get_campaign(campaign_id=campaign_id, scope=scope)
        readiness = evaluate_campaign_readiness(brief)
        if campaign.status is CampaignStatus.GENERATING:
            if brief_changed:
                raise InvalidCampaignTransitionError(
                    "Campaign Brief cannot change while generation is in progress"
                )
            return CampaignReadinessStateResult(campaign, readiness, False)
        if campaign.status is CampaignStatus.PLAN_READY and not brief_changed:
            return CampaignReadinessStateResult(campaign, readiness, False)

        target = status_for_readiness(readiness)
        if target is campaign.status:
            return CampaignReadinessStateResult(campaign, readiness, False)
        updated = await self._repository.update_status(
            campaign_id=campaign_id,
            scope=scope,
            status=target,
        )
        if updated is None:
            raise CampaignNotFoundError
        return CampaignReadinessStateResult(updated, readiness, True)


def _brief_changes(
    current: CampaignBrief,
    extracted_fields: CampaignBrief,
) -> dict[str, object]:
    return {
        name: candidate
        for name, candidate in extracted_fields.model_dump().items()
        if candidate is not None and candidate != getattr(current, name)
    }


def _repair_issues(
    error: ProviderResponseError | CampaignPlanValidationError,
) -> tuple[str, ...]:
    if isinstance(error, CampaignPlanValidationError):
        return error.issues
    return ("structured_output.invalid",)


__all__ = [
    "CampaignBriefStateResult",
    "CampaignBriefUpdateResult",
    "CampaignReadinessStateResult",
    "CampaignService",
]
