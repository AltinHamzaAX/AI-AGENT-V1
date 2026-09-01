from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies.campaigns import CampaignMessagingServiceDependency
from app.dependencies.conversations import ConversationScopeDependency
from app.integrations.llm import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from app.modules.campaigns.domain import (
    CampaignNotFoundError,
    InvalidCampaignTransitionError,
)
from app.modules.campaigns.schemas import CampaignMessageRequest, CampaignMessageResponse
from app.shared.conversations.domain import ConversationNotFoundError

router = APIRouter()


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
        raise HTTPException(status_code=404, detail="Campaign not found") from exc
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
