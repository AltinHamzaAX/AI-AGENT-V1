from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.infrastructure.database.base import Base
from app.modules.campaigns.domain import CampaignStatus


class CampaignModel(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('BRIEFING', 'READY', 'GENERATING', 'PLAN_READY')",
            name="valid_status",
        ),
        UniqueConstraint("conversation_id", name="uq_campaigns_conversation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CampaignStatus.BRIEFING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CampaignBriefModel(Base):
    __tablename__ = "campaign_briefs"
    __table_args__ = (
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="non_negative_budget",
        ),
    )

    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    business: Mapped[str | None] = mapped_column(String(500))
    product_or_service: Mapped[str | None] = mapped_column(String(500))
    goal: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(500))
    offer: Mapped[str | None] = mapped_column(Text)
    value_proposition: Mapped[str | None] = mapped_column(Text)
    channels: Mapped[list[str] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric())
    budget_currency: Mapped[str | None] = mapped_column(String(500))
    duration: Mapped[str | None] = mapped_column(String(500))
    brand_tone: Mapped[str | None] = mapped_column(Text)
    constraints: Mapped[list[str] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CampaignPlanModel(Base):
    __tablename__ = "campaign_plans"

    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["CampaignBriefModel", "CampaignModel", "CampaignPlanModel"]
