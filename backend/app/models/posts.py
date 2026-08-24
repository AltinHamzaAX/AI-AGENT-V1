from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.infrastructure.database.base import Base


class PostModel(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_scope", "user_id", "project_id", "id"),
        Index("ix_posts_campaign", "campaign_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
    )
    campaign_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    title: Mapped[str | None] = mapped_column(String(200))
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


class PostGenerationModel(Base):
    __tablename__ = "post_generations"
    __table_args__ = (
        CheckConstraint("attempt > 0", name="positive_attempt"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'reviewing', 'revision', "
            "'completed', 'failed', 'cancelled')",
            name="valid_status",
        ),
        UniqueConstraint("post_id", "attempt", name="uq_post_generations_post_attempt"),
        Index("ix_post_generations_post", "post_id", "attempt"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    post_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
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


class PostGenerationJobModel(Base):
    __tablename__ = "post_generation_jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="non_negative_attempts"),
        CheckConstraint("max_attempts > 0", name="positive_max_attempts"),
        CheckConstraint("timeout_seconds > 0", name="positive_timeout_seconds"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_scheduled', 'completed', "
            "'failed', 'dead')",
            name="valid_status",
        ),
        UniqueConstraint("generation_id", name="uq_post_generation_jobs_generation"),
        UniqueConstraint("idempotency_key", name="uq_post_generation_jobs_idempotency"),
        Index("ix_post_generation_jobs_claim", "status", "available_at", "leased_until"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    generation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_generations.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    last_error_code: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationArtifactModel(Base):
    __tablename__ = "generation_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('intermediate', 'preview', 'final')",
            name="valid_kind",
        ),
        CheckConstraint("size_bytes > 0", name="positive_size"),
        CheckConstraint("length(checksum) = 64", name="sha256_checksum"),
        CheckConstraint(
            "(width IS NULL AND height IS NULL) OR "
            "(width IS NOT NULL AND height IS NOT NULL AND width > 0 AND height > 0)",
            name="valid_dimensions",
        ),
        Index("ix_generation_artifacts_generation", "generation_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    generation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_generations.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
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


class PostGenerationStateModel(Base):
    __tablename__ = "post_generation_states"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="positive_schema_version"),
        CheckConstraint("version > 0", name="positive_version"),
    )

    generation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_generations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(
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


class PostGenerationStateVersionModel(Base):
    __tablename__ = "post_generation_state_versions"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="positive_schema_version"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_post_generation_state_versions_generation", "generation_id", "version"),
    )

    generation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_generations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_section: Mapped[str | None] = mapped_column(String(50))
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PostExecutionTraceModel(Base):
    __tablename__ = "post_execution_traces"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('agent', 'tool', 'provider', 'generation_step')",
            name="valid_kind",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'timeout', 'denied')",
            name="valid_status",
        ),
        CheckConstraint("duration_ms >= 0", name="non_negative_duration"),
        CheckConstraint("retry_count >= 0", name="non_negative_retries"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="non_negative_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="non_negative_output_tokens",
        ),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="non_negative_cost"),
        Index(
            "ix_post_execution_traces_generation_timeline",
            "generation_id",
            "started_at",
            "id",
        ),
        Index("ix_post_execution_traces_correlation", "correlation_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    generation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("post_generations.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_reference: Mapped[str | None] = mapped_column(String(71))
    output_reference: Mapped[str | None] = mapped_column(String(71))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(300))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(200))
    trace_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
