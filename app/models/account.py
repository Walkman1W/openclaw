from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    account_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    api_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unverified",
        server_default="unverified",
    )
    rep_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    verify_tasks_done: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    capability_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default="{}",
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
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

    __table_args__ = (
        CheckConstraint(
            "account_type IN ('human', 'agent', 'crayfish', 'admin')",
            name="ck_accounts_account_type",
        ),
        CheckConstraint(
            "status IN ('unverified', 'active', 'restricted', 'frozen')",
            name="ck_accounts_status",
        ),
        CheckConstraint(
            "rep_score >= 0",
            name="ck_accounts_rep_score_non_negative",
        ),
        # GIN index on capability_tags for array containment queries
        Index(
            "ix_accounts_capability_tags",
            "capability_tags",
            postgresql_using="gin",
        ),
        # Composite index for status-filtered reputation queries
        Index(
            "ix_accounts_status_rep_score",
            "status",
            "rep_score",
        ),
        # Partial index on email for human accounts only
        Index(
            "ix_accounts_email_human",
            "email",
            postgresql_where="account_type = 'human'",
        ),
    )
