from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.infrastructure.database.base import Base


class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("width > 0", name="positive_width"),
        CheckConstraint("height > 0", name="positive_height"),
        CheckConstraint("size_bytes > 0", name="positive_size"),
        CheckConstraint("length(checksum) = 64", name="sha256_checksum"),
        CheckConstraint(
            "role IN ('logo', 'product', 'vehicle', 'packaging', 'environment', "
            "'background', 'person', 'style_reference', 'inspiration', 'supporting_asset')",
            name="valid_role",
        ),
        UniqueConstraint("message_id", "checksum", name="uq_assets_message_checksum"),
        Index("ix_assets_scope", "user_id", "project_id", "id"),
        Index("ix_assets_scope_checksum", "user_id", "project_id", "checksum"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
