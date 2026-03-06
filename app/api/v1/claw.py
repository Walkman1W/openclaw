"""TASK-007: CLAW balance and transaction history routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.account import Account
from app.models.transaction import Transaction
from app.redis_client import get_redis
from app.schemas.claw import BalanceResponse, TransactionListResponse, TransactionRecord
from app.services.auth_service import get_admin_account, get_current_account
from app.services.claw_service import get_balance

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /claw/balance  — authenticated user's own balance
# ---------------------------------------------------------------------------


@router.get("/balance", response_model=BalanceResponse)
async def read_own_balance(
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> BalanceResponse:
    """Return the authenticated account's current CLAW balance."""
    cache_key = f"balance:{current_account.id}"
    cached = False

    # Check cache first to report the 'cached' flag accurately
    try:
        raw = await redis.get(cache_key)
        if raw is not None:
            cached = True
    except Exception:
        pass

    balance = await get_balance(db, current_account.id, redis=redis)

    return BalanceResponse(
        account_id=current_account.id,
        balance=balance,
        cached=cached,
    )


# ---------------------------------------------------------------------------
# GET /claw/transactions  — paginated ledger for the authenticated account
# ---------------------------------------------------------------------------


@router.get("/transactions", response_model=TransactionListResponse)
async def read_own_transactions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> TransactionListResponse:
    """Return a paginated list of CLAW transactions for the authenticated account."""
    # Total count
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.account_id == current_account.id
        )
    )
    total = count_result.scalar() or 0

    # Paginated rows, newest first
    rows_result = await db.execute(
        select(Transaction)
        .where(Transaction.account_id == current_account.id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = rows_result.scalars().all()

    return TransactionListResponse(
        transactions=[TransactionRecord.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /claw/balance/{account_id}  — admin-only arbitrary balance lookup
# ---------------------------------------------------------------------------


@router.get("/balance/{account_id}", response_model=BalanceResponse)
async def read_balance_admin(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    _admin: bool = Depends(get_admin_account),
) -> BalanceResponse:
    """Admin endpoint: return the CLAW balance for any account."""
    # Verify the account exists
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    cache_key = f"balance:{account_id}"
    cached = False
    try:
        raw = await redis.get(cache_key)
        if raw is not None:
            cached = True
    except Exception:
        pass

    balance = await get_balance(db, account_id, redis=redis)

    return BalanceResponse(
        account_id=account_id,
        balance=balance,
        cached=cached,
    )
