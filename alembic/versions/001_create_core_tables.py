"""create core tables

Revision ID: 001
Revises:
Create Date: 2026-03-06

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. accounts
    # ------------------------------------------------------------------
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True, unique=True),
        sa.Column("api_key_hash", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("rep_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verify_tasks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "capability_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "account_type IN ('human', 'agent', 'crayfish', 'admin')",
            name="ck_accounts_account_type",
        ),
        sa.CheckConstraint(
            "status IN ('unverified', 'active', 'restricted', 'frozen')",
            name="ck_accounts_status",
        ),
        sa.CheckConstraint(
            "rep_score >= 0",
            name="ck_accounts_rep_score_non_negative",
        ),
    )

    # GIN index on capability_tags
    op.create_index(
        "ix_accounts_capability_tags",
        "accounts",
        ["capability_tags"],
        postgresql_using="gin",
    )
    # Composite index for status + reputation filtering
    op.create_index(
        "ix_accounts_status_rep_score",
        "accounts",
        ["status", "rep_score"],
    )
    # Partial index on email for human accounts only
    op.create_index(
        "ix_accounts_email_human",
        "accounts",
        ["email"],
        postgresql_where=sa.text("account_type = 'human'"),
    )

    # ------------------------------------------------------------------
    # 2. tasks
    # ------------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "publisher_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("task_level", sa.SmallInteger(), nullable=False),
        sa.Column("input_spec", postgresql.JSONB(), nullable=False),
        sa.Column("output_spec", postgresql.JSONB(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("reward_pool", sa.Integer(), nullable=False),
        sa.Column("deposit_held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "required_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "preferred_tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("min_reputation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "audit_mode",
            sa.String(30),
            nullable=False,
            server_default="auto+sample",
        ),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "task_level BETWEEN 1 AND 4",
            name="ck_tasks_task_level_range",
        ),
        sa.CheckConstraint(
            "reward_pool >= 5",
            name="ck_tasks_reward_pool_min",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending','claimed','in_progress','submitted',"
            "'under_audit','settled','disputed','settled_override','failed'"
            ")",
            name="ck_tasks_status",
        ),
    )

    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index(
        "ix_tasks_required_tags",
        "tasks",
        ["required_tags"],
        postgresql_using="gin",
    )
    # Partial index: deadline matters only for tasks not yet settled/failed
    op.create_index(
        "ix_tasks_deadline_active",
        "tasks",
        ["deadline"],
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )
    op.create_index("ix_tasks_publisher_id", "tasks", ["publisher_id"])
    op.create_index("ix_tasks_assignee_id", "tasks", ["assignee_id"])

    # ------------------------------------------------------------------
    # 3. transactions  (append-only ledger)
    # ------------------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("tx_type", sa.String(30), nullable=False),
        # task_id FK is deferred (use_alter); added below via ADD CONSTRAINT
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tx_type IN ("
            "'reward','deposit','deposit_refund','deposit_burn',"
            "'fee','mint','burn','audit_reward','arbitration_reward','registration_fee'"
            ")",
            name="ck_transactions_tx_type",
        ),
    )

    # Deferred FK: transactions.task_id -> tasks.id
    # Added via ALTER TABLE so it doesn't block table creation order.
    op.create_foreign_key(
        "fk_transactions_task_id",
        "transactions",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_task_id", "transactions", ["task_id"])
    op.create_index(
        "ix_transactions_account_created_desc",
        "transactions",
        ["account_id", "created_at"],
    )

    # Row-Level Security on transactions (append-only enforcement at DB level)
    op.execute("ALTER TABLE transactions ENABLE ROW LEVEL SECURITY")

    # ------------------------------------------------------------------
    # 4. task_status_history
    # ------------------------------------------------------------------
    op.create_table(
        "task_status_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column(
            "triggered_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_task_status_history_task_id", "task_status_history", ["task_id"]
    )
    op.create_index(
        "ix_task_status_history_task_created",
        "task_status_history",
        ["task_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 5. audit_logs
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer", sa.SmallInteger(), nullable=False),
        sa.Column(
            "auditor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "layer IN (0, 1, 2)",
            name="ck_audit_logs_layer",
        ),
        sa.CheckConstraint(
            "result IN ('pass', 'fail', 'partial')",
            name="ck_audit_logs_result",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.000 AND confidence <= 1.000)",
            name="ck_audit_logs_confidence_range",
        ),
    )

    op.create_index("ix_audit_logs_task_id", "audit_logs", ["task_id"])
    op.create_index("ix_audit_logs_layer_result", "audit_logs", ["layer", "result"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("task_status_history")
    op.drop_constraint("fk_transactions_task_id", "transactions", type_="foreignkey")
    op.drop_table("transactions")
    op.drop_table("tasks")
    op.drop_table("accounts")
