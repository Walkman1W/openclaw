from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    publisher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    task_level: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    input_spec: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    output_spec: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    acceptance_criteria: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    reward_pool: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    deposit_held: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    required_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default="{}",
    )
    preferred_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default="{}",
    )
    min_reputation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    audit_mode: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="auto+sample",
        server_default="auto+sample",
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    result_data: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    # Internal metadata (honeypot answers, crayfish config, etc.) — not exposed in API
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
            "task_level BETWEEN 1 AND 4",
            name="ck_tasks_task_level_range",
        ),
        CheckConstraint(
            "reward_pool >= 5",
            name="ck_tasks_reward_pool_min",
        ),
        CheckConstraint(
            "status IN ("
            "'pending','claimed','in_progress','submitted',"
            "'under_audit','settled','disputed','settled_override','failed'"
            ")",
            name="ck_tasks_status",
        ),
        # Index on status for filtering by task state
        Index("ix_tasks_status", "status"),
        # GIN index on required_tags for array containment queries
        Index(
            "ix_tasks_required_tags",
            "required_tags",
            postgresql_using="gin",
        ),
        # Partial index on deadline for active tasks only
        Index(
            "ix_tasks_deadline_active",
            "deadline",
            postgresql_where="status IN ('pending', 'claimed')",
        ),
        # publisher_id and assignee_id are indexed via index=True on the columns above
    )
