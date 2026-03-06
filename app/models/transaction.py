from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    balance_after: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    tx_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    # task_id uses use_alter=True to break the circular dependency:
    # tasks table is created after transactions in some migration orderings,
    # so the FK constraint is added via ALTER TABLE after both tables exist.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tasks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_transactions_task_id",
        ),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "tx_type IN ("
            "'reward','deposit','deposit_refund','deposit_burn',"
            "'fee','mint','burn','audit_reward','arbitration_reward','registration_fee'"
            ")",
            name="ck_transactions_tx_type",
        ),
        # Index for per-account ledger queries
        Index("ix_transactions_account_id", "account_id"),
        # Index for task-related transaction lookups
        Index("ix_transactions_task_id", "task_id"),
        # Composite index for chronological per-account ledger reads (most recent first)
        Index(
            "ix_transactions_account_created_desc",
            "account_id",
            "created_at",
        ),
    )
