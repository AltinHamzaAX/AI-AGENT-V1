import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.dependencies.campaigns import (
    CampaignExportServiceDependency,
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
    CampaignExportError,
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
logger = logging.getLogger(__name__)


def _campaign_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Campaign not found")


def _unexpected_campaign_error(
    *,
    operation: str,
    campaign_id: UUID | None,
    error: Exception,
) -> HTTPException:
    logger.error(
        "Unexpected Campaign operation failure",
        extra={
            "operation": operation,
            "campaign_id": str(campaign_id) if campaign_id is not None else None,
            "error_type": type(error).__name__,
        },
    )
    return HTTPException(status_code=500, detail="Campaign operation failed")


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
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="create",
            campaign_id=None,
            error=exc,
        ) from exc
    return CreateCampaignResponse.from_domain(campaign)


@router.get("", response_model=CreateCampaignResponse)
async def find_campaign_by_conversation(
    conversation_id: UUID,
    scope: ConversationScopeDependency,
    service: CampaignServiceDependency,
) -> CreateCampaignResponse:
    try:
        campaign = await service.find_campaign_by_conversation(
            conversation_id=conversation_id,
            scope=scope,
        )
        if campaign is None:
            raise CampaignNotFoundError
    except CampaignNotFoundError as exc:
        raise _campaign_not_found() from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign retrieval is temporarily unavailable",
        ) from exc
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="find_by_conversation",
            campaign_id=None,
            error=exc,
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
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="retrieve",
            campaign_id=campaign_id,
            error=exc,
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
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="generate",
            campaign_id=campaign_id,
            error=exc,
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
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="retrieve_plan",
            campaign_id=campaign_id,
            error=exc,
        ) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Campaign Plan not available")
    return plan


@router.get(
    "/{campaign_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def export_campaign(
    campaign_id: UUID,
    scope: ConversationScopeDependency,
    campaigns: CampaignServiceDependency,
    exporter: CampaignExportServiceDependency,
) -> Response:
    try:
        await campaigns.get_campaign(campaign_id=campaign_id, scope=scope)
        brief = await campaigns.get_brief(campaign_id=campaign_id, scope=scope)
        plan = await campaigns.get_plan(campaign_id=campaign_id, scope=scope)
    except CampaignNotFoundError as exc:
        raise _campaign_not_found() from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign export is temporarily unavailable",
        ) from exc
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="load_export",
            campaign_id=campaign_id,
            error=exc,
        ) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Campaign Plan not available")
    try:
        result = exporter.export(brief=brief, plan=plan)
    except CampaignExportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Campaign export could not be created",
        ) from exc
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="render_export",
            campaign_id=campaign_id,
            error=exc,
        ) from exc
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


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
    except Exception as exc:
        raise _unexpected_campaign_error(
            operation="message",
            campaign_id=campaign_id,
            error=exc,
        ) from exc
    return CampaignMessageResponse(
        reply=result.reply,
        status=result.status,
        brief=result.brief,
    )
