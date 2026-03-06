"""
Tests for ORM model definitions.

These tests validate model structure without requiring a live database
connection. They use sqlalchemy inspection and ast.parse() syntax checks.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest
from sqlalchemy import inspect as sa_inspect, String, Integer, SmallInteger, BigInteger, Text, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_column(mapper, col_name):
    """Return a Column object from a mapped class by attribute name."""
    return mapper.__table__.c[col_name]


# ---------------------------------------------------------------------------
# 1. Import sanity – all models must import without error
# ---------------------------------------------------------------------------

class TestModelImports:
    def test_account_imports(self):
        from app.models.account import Account
        assert Account is not None

    def test_transaction_imports(self):
        from app.models.transaction import Transaction
        assert Transaction is not None

    def test_task_imports(self):
        from app.models.task import Task
        assert Task is not None

    def test_task_status_history_imports(self):
        from app.models.task_status_history import TaskStatusHistory
        assert TaskStatusHistory is not None

    def test_audit_log_imports(self):
        from app.models.audit_log import AuditLog
        assert AuditLog is not None

    def test_package_init_exports_all(self):
        import app.models as models_pkg
        for name in ["Account", "Transaction", "Task", "TaskStatusHistory", "AuditLog"]:
            assert hasattr(models_pkg, name), f"app.models missing export: {name}"


# ---------------------------------------------------------------------------
# 2. Table names
# ---------------------------------------------------------------------------

class TestTableNames:
    def test_account_table_name(self):
        from app.models.account import Account
        assert Account.__tablename__ == "accounts"

    def test_transaction_table_name(self):
        from app.models.transaction import Transaction
        assert Transaction.__tablename__ == "transactions"

    def test_task_table_name(self):
        from app.models.task import Task
        assert Task.__tablename__ == "tasks"

    def test_task_status_history_table_name(self):
        from app.models.task_status_history import TaskStatusHistory
        assert TaskStatusHistory.__tablename__ == "task_status_history"

    def test_audit_log_table_name(self):
        from app.models.audit_log import AuditLog
        assert AuditLog.__tablename__ == "audit_logs"


# ---------------------------------------------------------------------------
# 3. Account column types and constraints
# ---------------------------------------------------------------------------

class TestAccountModel:
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.models.account import Account
        self.model = Account
        self.table = Account.__table__

    def test_id_is_primary_key(self):
        col = self.table.c["id"]
        assert col.primary_key

    def test_id_type_is_uuid(self):
        col = self.table.c["id"]
        assert isinstance(col.type, UUID)

    def test_account_type_not_nullable(self):
        col = self.table.c["account_type"]
        assert not col.nullable

    def test_account_type_max_length(self):
        col = self.table.c["account_type"]
        assert col.type.length == 20

    def test_email_is_nullable_and_unique(self):
        col = self.table.c["email"]
        assert col.nullable
        assert any(uc for uc in self.table.constraints
                   if hasattr(uc, "columns") and "email" in [c.name for c in uc.columns])

    def test_api_key_hash_length(self):
        col = self.table.c["api_key_hash"]
        assert col.type.length == 64

    def test_status_server_default(self):
        col = self.table.c["status"]
        assert col.server_default is not None

    def test_rep_score_is_integer(self):
        col = self.table.c["rep_score"]
        assert isinstance(col.type, Integer)

    def test_capability_tags_is_array(self):
        col = self.table.c["capability_tags"]
        assert isinstance(col.type, ARRAY)

    def test_metadata_is_jsonb(self):
        col = self.table.c["metadata"]
        assert isinstance(col.type, JSONB)

    def test_created_at_timezone_aware(self):
        col = self.table.c["created_at"]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True

    def test_updated_at_timezone_aware(self):
        col = self.table.c["updated_at"]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True

    def test_check_constraints_defined(self):
        from sqlalchemy import CheckConstraint
        constraint_names = {
            c.name for c in self.table.constraints
            if isinstance(c, CheckConstraint)
        }
        assert "ck_accounts_account_type" in constraint_names
        assert "ck_accounts_status" in constraint_names
        assert "ck_accounts_rep_score_non_negative" in constraint_names

    def test_gin_index_on_capability_tags(self):
        index_names = {idx.name for idx in self.table.indexes}
        assert "ix_accounts_capability_tags" in index_names

    def test_status_rep_score_index(self):
        index_names = {idx.name for idx in self.table.indexes}
        assert "ix_accounts_status_rep_score" in index_names

    def test_partial_email_index(self):
        index_names = {idx.name for idx in self.table.indexes}
        assert "ix_accounts_email_human" in index_names


# ---------------------------------------------------------------------------
# 4. Transaction model – no updated_at, has use_alter FK
# ---------------------------------------------------------------------------

class TestTransactionModel:
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.models.transaction import Transaction
        self.model = Transaction
        self.table = Transaction.__table__

    def test_id_is_biginteger(self):
        col = self.table.c["id"]
        assert isinstance(col.type, BigInteger)

    def test_no_updated_at_column(self):
        assert "updated_at" not in self.table.c

    def test_amount_is_integer(self):
        col = self.table.c["amount"]
        assert isinstance(col.type, Integer)

    def test_balance_after_not_nullable(self):
        col = self.table.c["balance_after"]
        assert not col.nullable

    def test_tx_type_max_length(self):
        col = self.table.c["tx_type"]
        assert col.type.length == 30

    def test_task_id_is_nullable(self):
        col = self.table.c["task_id"]
        assert col.nullable

    def test_account_id_not_nullable(self):
        col = self.table.c["account_id"]
        assert not col.nullable

    def test_check_constraint_tx_type(self):
        from sqlalchemy import CheckConstraint
        names = {c.name for c in self.table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_transactions_tx_type" in names

    def test_indexes_present(self):
        idx_names = {idx.name for idx in self.table.indexes}
        assert "ix_transactions_account_id" in idx_names
        assert "ix_transactions_task_id" in idx_names
        assert "ix_transactions_account_created_desc" in idx_names


# ---------------------------------------------------------------------------
# 5. Task model
# ---------------------------------------------------------------------------

class TestTaskModel:
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.models.task import Task
        self.model = Task
        self.table = Task.__table__

    def test_task_level_is_smallinteger(self):
        col = self.table.c["task_level"]
        assert isinstance(col.type, SmallInteger)

    def test_input_spec_is_jsonb(self):
        col = self.table.c["input_spec"]
        assert isinstance(col.type, JSONB)

    def test_acceptance_criteria_is_jsonb(self):
        col = self.table.c["acceptance_criteria"]
        assert isinstance(col.type, JSONB)

    def test_required_tags_is_array(self):
        col = self.table.c["required_tags"]
        assert isinstance(col.type, ARRAY)

    def test_preferred_tags_is_array(self):
        col = self.table.c["preferred_tags"]
        assert isinstance(col.type, ARRAY)

    def test_content_hash_length(self):
        col = self.table.c["content_hash"]
        assert col.type.length == 64

    def test_status_default(self):
        col = self.table.c["status"]
        assert col.server_default is not None

    def test_check_constraints(self):
        from sqlalchemy import CheckConstraint
        names = {c.name for c in self.table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_tasks_task_level_range" in names
        assert "ck_tasks_reward_pool_min" in names
        assert "ck_tasks_status" in names

    def test_gin_index_required_tags(self):
        idx_names = {idx.name for idx in self.table.indexes}
        assert "ix_tasks_required_tags" in idx_names


# ---------------------------------------------------------------------------
# 6. TaskStatusHistory model
# ---------------------------------------------------------------------------

class TestTaskStatusHistoryModel:
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.models.task_status_history import TaskStatusHistory
        self.model = TaskStatusHistory
        self.table = TaskStatusHistory.__table__

    def test_id_is_biginteger(self):
        col = self.table.c["id"]
        assert isinstance(col.type, BigInteger)

    def test_from_status_nullable(self):
        col = self.table.c["from_status"]
        assert col.nullable

    def test_to_status_not_nullable(self):
        col = self.table.c["to_status"]
        assert not col.nullable

    def test_indexes(self):
        idx_names = {idx.name for idx in self.table.indexes}
        assert "ix_task_status_history_task_id" in idx_names
        assert "ix_task_status_history_task_created" in idx_names


# ---------------------------------------------------------------------------
# 7. AuditLog model
# ---------------------------------------------------------------------------

class TestAuditLogModel:
    @pytest.fixture(autouse=True)
    def _import(self):
        from app.models.audit_log import AuditLog
        self.model = AuditLog
        self.table = AuditLog.__table__

    def test_id_is_uuid(self):
        col = self.table.c["id"]
        assert isinstance(col.type, UUID)

    def test_layer_is_smallinteger(self):
        col = self.table.c["layer"]
        assert isinstance(col.type, SmallInteger)

    def test_auditor_id_nullable(self):
        col = self.table.c["auditor_id"]
        assert col.nullable

    def test_confidence_is_numeric(self):
        col = self.table.c["confidence"]
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 4
        assert col.type.scale == 3

    def test_checks_is_jsonb(self):
        col = self.table.c["checks"]
        assert isinstance(col.type, JSONB)

    def test_check_constraints(self):
        from sqlalchemy import CheckConstraint
        names = {c.name for c in self.table.constraints if isinstance(c, CheckConstraint)}
        assert "ck_audit_logs_layer" in names
        assert "ck_audit_logs_result" in names
        assert "ck_audit_logs_confidence_range" in names

    def test_indexes(self):
        idx_names = {idx.name for idx in self.table.indexes}
        assert "ix_audit_logs_task_id" in idx_names
        assert "ix_audit_logs_layer_result" in idx_names


# ---------------------------------------------------------------------------
# 8. Syntax check – all generated files parse cleanly via ast.parse()
# ---------------------------------------------------------------------------

class TestSyntaxCheck:
    ROOT = pathlib.Path("/mnt/c/Users/LENOVO/Documents/Playground/openclaw")

    FILES_TO_CHECK = [
        "app/models/account.py",
        "app/models/transaction.py",
        "app/models/task.py",
        "app/models/task_status_history.py",
        "app/models/audit_log.py",
        "app/models/__init__.py",
        "alembic/versions/001_create_core_tables.py",
    ]

    @pytest.mark.parametrize("rel_path", FILES_TO_CHECK)
    def test_file_syntax(self, rel_path):
        path = self.ROOT / rel_path
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"SyntaxError in {rel_path}: {exc}")
