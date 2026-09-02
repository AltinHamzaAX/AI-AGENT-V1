from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.dependencies.campaigns import (
    CampaignMessagingServiceDependency,
    CampaignPlanGeneratorDependency,
    CampaignServiceDependency,
)
from app.dependencies.conversations import ConversationScopeDependency
from app.integrations.llm import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from app.modules.campaigns.domain import (
    CampaignNotFoundError,
    CampaignPlanValidationError,
    CampaignSourceNotFoundError,
    InvalidCampaignTransitionError,
)
from app.modules.campaigns.schemas import (
    CampaignDetailResponse,
    CampaignMessageRequest,
    CampaignMessageResponse,
    CampaignPlan,
    CreateCampaignRequest,
    CreateCampaignResponse,
    GenerateCampaignResponse,
)
from app.shared.conversations.domain import ConversationNotFoundError

router = APIRouter()


def _campaign_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Campaign not found")


@router.post(
    "",
    response_model=CreateCampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    payload: CreateCampaignRequest,
    scope: ConversationScopeDependency,
    service: CampaignServiceDependency,
) -> CreateCampaignResponse:
    try:
        campaign = await service.create_campaign(
            conversation_id=payload.conversation_id,
            scope=scope,
            initial_brief=payload.brief,
        )
    except CampaignSourceNotFoundError as exc:
        raise _campaign_not_found() from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="A Campaign already exists for this conversation",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign creation is temporarily unavailable",
        ) from exc
    return CreateCampaignResponse.from_domain(campaign)


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    campaign_id: UUID,
    scope: ConversationScopeDependency,
    service: CampaignServiceDependency,
) -> CampaignDetailResponse:
    try:
        campaign = await service.get_campaign(campaign_id=campaign_id, scope=scope)
        brief = await service.get_brief(campaign_id=campaign_id, scope=scope)
        plan = await service.get_plan(campaign_id=campaign_id, scope=scope)
    except CampaignNotFoundError as exc:
        raise _campaign_not_found() from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign retrieval is temporarily unavailable",
        ) from exc
    return CampaignDetailResponse.from_domain(
        campaign,
        brief=brief,
        plan_available=plan is not None,
    )


@router.post(
    "/{campaign_id}/generate",
    response_model=GenerateCampaignResponse,
)
async def generate_campaign(
    campaign_id: UUID,
    scope: ConversationScopeDependency,
    service: CampaignServiceDependency,
    generator: CampaignPlanGeneratorDependency,
) -> GenerateCampaignResponse:
    try:
        plan = await service.generate_plan(
            campaign_id=campaign_id,
            scope=scope,
            generator=generator,
        )
        campaign = await service.get_campaign(campaign_id=campaign_id, scope=scope)
    except CampaignNotFoundError as exc:
        raise _campaign_not_found() from exc
    except InvalidCampaignTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProviderRateLimitError, ProviderQuotaError) as exc:
        raise HTTPException(
            status_code=429,
            detail="Campaign Plan generation is rate limited; try again shortly",
        ) from exc
    except (ProviderError, CampaignPlanValidationError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Campaign Plan generation failed; try again",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign Plan generation is temporarily unavailable",
        ) from exc
    return GenerateCampaignResponse(status=campaign.status, plan=plan)


@router.get("/{campaign_id}/plan", response_model=CampaignPlan)
async def get_campaign_plan(
    campaign_id: UUID,
    scope: ConversationScopeDependency,
    service: CampaignServiceDependency,
) -> CampaignPlan:
    try:
        plan = await service.get_plan(campaign_id=campaign_id, scope=scope)
    except CampaignNotFoundError as exc:
        raise _campaign_not_found() from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign Plan retrieval is temporarily unavailable",
        ) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Campaign Plan not available")
    return plan


@router.post(
    "/{campaign_id}/messages",
    response_model=CampaignMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def campaign_message(
    campaign_id: UUID,
    payload: CampaignMessageRequest,
    scope: ConversationScopeDependency,
    service: CampaignMessagingServiceDependency,
) -> CampaignMessageResponse:
    try:
        result = await service.reply(
            campaign_id=campaign_id,
            scope=scope,
            message=payload.message,
        )
    except (CampaignNotFoundError, ConversationNotFoundError) as exc:
        raise _campaign_not_found() from exc
    except InvalidCampaignTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProviderRateLimitError, ProviderQuotaError) as exc:
        raise HTTPException(
            status_code=429,
            detail="The campaign assistant is rate limited; try again shortly",
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The campaign assistant is temporarily unavailable; the turn was not saved",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign messaging is temporarily unavailable",
        ) from exc
    return CampaignMessageResponse(
        reply=result.reply,
        status=result.status,
        brief=result.brief,
    )
