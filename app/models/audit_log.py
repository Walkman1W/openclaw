from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    layer: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    auditor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    result: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
    )
    checks: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "layer IN (0, 1, 2)",
            name="ck_audit_logs_layer",
        ),
        CheckConstraint(
            "result IN ('pass', 'fail', 'partial')",
            name="ck_audit_logs_result",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.000 AND confidence <= 1.000)",
            name="ck_audit_logs_confidence_range",
        ),
        Index("ix_audit_logs_task_id", "task_id"),
        Index("ix_audit_logs_layer_result", "layer", "result"),
    )
