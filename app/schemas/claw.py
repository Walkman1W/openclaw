"""TASK-007: Pydantic schemas for CLAW balance and transaction endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class BalanceResponse(BaseModel):
    account_id: uuid.UUID
    balance: int
    cached: bool


class TransactionRecord(BaseModel):
    id: int
    amount: int
    balance_after: int
    tx_type: str
    reason: str | None
    task_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    transactions: list[TransactionRecord]
    total: int
