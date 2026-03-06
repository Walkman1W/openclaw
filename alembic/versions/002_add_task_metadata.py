"""add metadata column to tasks

Revision ID: 002
Revises: 001
Create Date: 2026-03-06

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "metadata")
