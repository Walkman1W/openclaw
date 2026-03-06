"""TASK-006: CLAW points core engine.

All credit mutations MUST go through this module.  The service layer never
commits — the caller (route handler) owns the transaction boundary.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class InsufficientBalanceError(Exception):
    """Raised when a debit would push balance below zero."""


# ---------------------------------------------------------------------------
# Internal Redis helper
# ---------------------------------------------------------------------------

_BALANCE_TTL = 30  # seconds


async def _get_redis_client():
    """Return a Redis client from the shared connection pool.

    The redis_client module exposes an async-generator; we consume one value
    from it here so service functions can use Redis without FastAPI Depends().
    """
    from app.redis_client import get_redis  # local import avoids circular deps

    gen = get_redis()
    client = await gen.__anext__()
    return client, gen


async def _close_redis_client(client, gen) -> None:
    try:
        await gen.aclose()
    except StopAsyncIteration:
        pass


# ---------------------------------------------------------------------------
# Balance helpers
# ---------------------------------------------------------------------------


async def _compute_balance_from_db(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Aggregate balance directly from the transactions table."""
    result = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.account_id == account_id
        )
    )
    total = result.scalar()
    return int(total) if total is not None else 0


async def get_balance(
    db: AsyncSession,
    account_id: uuid.UUID,
    redis=None,
) -> int:
    """Return the current CLAW balance for *account_id*.

    Checks Redis cache first (key ``balance:{account_id}``, TTL 30 s).
    Falls back to a DB aggregate and back-fills the cache.

    Raises RuntimeError if the computed balance is negative (data inconsistency).
    """
    cache_key = f"balance:{account_id}"
    _own_redis = redis is None
    _gen = None

    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None

    cached = False
    balance: int | None = None

    if redis is not None:
        try:
            raw = await redis.get(cache_key)
            if raw is not None:
                balance = int(raw)
                cached = True
        except Exception:
            pass

    if balance is None:
        balance = await _compute_balance_from_db(db, account_id)
        if redis is not None:
            try:
                await redis.set(cache_key, balance, ex=_BALANCE_TTL)
            except Exception:
                pass

    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    if balance < 0:
        raise RuntimeError(
            f"Negative balance detected for account {account_id}: {balance}"
        )

    return balance


async def _invalidate_cache(redis, account_id: uuid.UUID) -> None:
    if redis is None:
        return
    try:
        await redis.delete(f"balance:{account_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core mutation operations
# ---------------------------------------------------------------------------


async def mint_claw(
    db: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    reason: str,
    operator_id: Optional[uuid.UUID] = None,
    task_id: Optional[uuid.UUID] = None,
    redis=None,
) -> Transaction:
    """Credit *amount* CLAW to *account_id*.

    Inserts an append-only Transaction row.  The caller owns commit/rollback.
    """
    if amount <= 0:
        raise ValueError(f"mint amount must be > 0, got {amount}")

    # Lock the account row to serialize concurrent mutations
    await db.execute(
        select(Account).where(Account.id == account_id).with_for_update()
    )

    current = await _compute_balance_from_db(db, account_id)
    balance_after = current + amount

    tx = Transaction(
        account_id=account_id,
        amount=amount,
        balance_after=balance_after,
        tx_type="mint",
        reason=reason,
        operator_id=operator_id,
        task_id=task_id,
    )
    db.add(tx)
    await db.flush()  # populate tx.id without committing

    # Invalidate cache
    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return tx


async def burn_claw(
    db: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    reason: str,
    operator_id: Optional[uuid.UUID] = None,
    task_id: Optional[uuid.UUID] = None,
    redis=None,
) -> Transaction:
    """Debit *amount* CLAW from *account_id*.

    Raises InsufficientBalanceError if balance < amount.
    The caller owns commit/rollback.
    """
    if amount <= 0:
        raise ValueError(f"burn amount must be > 0, got {amount}")

    await db.execute(
        select(Account).where(Account.id == account_id).with_for_update()
    )

    current = await _compute_balance_from_db(db, account_id)
    if current < amount:
        raise InsufficientBalanceError(
            f"Account {account_id} has {current} CLAW, cannot burn {amount}"
        )

    balance_after = current - amount

    tx = Transaction(
        account_id=account_id,
        amount=-amount,
        balance_after=balance_after,
        tx_type="burn",
        reason=reason,
        operator_id=operator_id,
        task_id=task_id,
    )
    db.add(tx)
    await db.flush()

    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return tx


async def transfer_claw(
    db: AsyncSession,
    from_account_id: uuid.UUID,
    to_account_id: uuid.UUID,
    amount: int,
    tx_type_from: str,
    tx_type_to: str,
    reason: str,
    task_id: Optional[uuid.UUID] = None,
    redis=None,
) -> tuple[Transaction, Transaction]:
    """Atomically move *amount* CLAW from one account to another.

    Locks are acquired in UUID sort order to prevent deadlocks.
    The caller owns commit/rollback.
    """
    if amount <= 0:
        raise ValueError(f"transfer amount must be > 0, got {amount}")

    # Sort UUIDs to establish a consistent locking order (deadlock prevention)
    ids_ordered = sorted([from_account_id, to_account_id], key=lambda u: u.bytes)
    for acct_id in ids_ordered:
        await db.execute(
            select(Account).where(Account.id == acct_id).with_for_update()
        )

    from_balance = await _compute_balance_from_db(db, from_account_id)
    if from_balance < amount:
        raise InsufficientBalanceError(
            f"Account {from_account_id} has {from_balance} CLAW, cannot transfer {amount}"
        )

    to_balance = await _compute_balance_from_db(db, to_account_id)

    from_tx = Transaction(
        account_id=from_account_id,
        amount=-amount,
        balance_after=from_balance - amount,
        tx_type=tx_type_from,
        reason=reason,
        task_id=task_id,
    )
    to_tx = Transaction(
        account_id=to_account_id,
        amount=amount,
        balance_after=to_balance + amount,
        tx_type=tx_type_to,
        reason=reason,
        task_id=task_id,
    )
    db.add(from_tx)
    db.add(to_tx)
    await db.flush()

    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, from_account_id)
    await _invalidate_cache(redis, to_account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return from_tx, to_tx


async def lock_claw(
    db: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    task_id: uuid.UUID,
    reason: str = "task_deposit",
    redis=None,
) -> Transaction:
    """Lock *amount* CLAW from *account_id* into a task's reward pool.

    Writes a 'deposit' transaction (negative amount).
    Call unlock_claw to refund on task cancellation.
    """
    if amount <= 0:
        raise ValueError(f"lock amount must be > 0, got {amount}")

    await db.execute(
        select(Account).where(Account.id == account_id).with_for_update()
    )

    current = await _compute_balance_from_db(db, account_id)
    if current < amount:
        raise InsufficientBalanceError(
            f"Account {account_id} has {current} CLAW, cannot lock {amount}"
        )

    balance_after = current - amount

    tx = Transaction(
        account_id=account_id,
        amount=-amount,
        balance_after=balance_after,
        tx_type="deposit",
        reason=reason,
        task_id=task_id,
    )
    db.add(tx)
    await db.flush()

    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return tx


async def unlock_claw(
    db: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    task_id: uuid.UUID,
    reason: str = "task_deposit_refund",
    redis=None,
) -> Transaction:
    """Refund a previously locked amount back to *account_id*.

    Writes a 'deposit_refund' transaction (positive amount).
    """
    if amount <= 0:
        raise ValueError(f"unlock amount must be > 0, got {amount}")

    await db.execute(
        select(Account).where(Account.id == account_id).with_for_update()
    )

    current = await _compute_balance_from_db(db, account_id)
    balance_after = current + amount

    tx = Transaction(
        account_id=account_id,
        amount=amount,
        balance_after=balance_after,
        tx_type="deposit_refund",
        reason=reason,
        task_id=task_id,
    )
    db.add(tx)
    await db.flush()

    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return tx


# ---------------------------------------------------------------------------
# Task settlement
# ---------------------------------------------------------------------------

_WORKER_SHARE = 0.80
_PLATFORM_SHARE = 0.20


async def settle_task_reward(
    db: AsyncSession,
    task_id: uuid.UUID,
    publisher_id: uuid.UUID,
    worker_id: uuid.UUID,
    platform_account_id: uuid.UUID,
    reward_pool: int,
    redis=None,
) -> dict:
    """Distribute *reward_pool* CLAW at MVP 80/20 split.

    worker  receives 80 % (floor division).
    platform receives the remainder (reward_pool - worker_amount).

    All three account locks are acquired in UUID sort order.
    The caller owns commit/rollback.

    Returns::

        {"worker_amount": int, "platform_amount": int}
    """
    if reward_pool <= 0:
        raise ValueError(f"reward_pool must be > 0, got {reward_pool}")

    worker_amount = int(reward_pool * _WORKER_SHARE)
    platform_amount = reward_pool - worker_amount

    # Acquire locks in deterministic order
    ids_ordered = sorted(
        [publisher_id, worker_id, platform_account_id], key=lambda u: u.bytes
    )
    for acct_id in ids_ordered:
        await db.execute(
            select(Account).where(Account.id == acct_id).with_for_update()
        )

    # The reward pool was already deducted from publisher at task-publish time
    # (via lock_claw).  We now credit the recipients from that locked pool.
    worker_balance = await _compute_balance_from_db(db, worker_id)
    platform_balance = await _compute_balance_from_db(db, platform_account_id)

    worker_tx = Transaction(
        account_id=worker_id,
        amount=worker_amount,
        balance_after=worker_balance + worker_amount,
        tx_type="reward",
        reason="task_settlement",
        task_id=task_id,
    )
    platform_tx = Transaction(
        account_id=platform_account_id,
        amount=platform_amount,
        balance_after=platform_balance + platform_amount,
        tx_type="fee",
        reason="task_settlement_fee",
        task_id=task_id,
    )
    db.add(worker_tx)
    db.add(platform_tx)
    await db.flush()

    _own_redis = redis is None
    _gen = None
    if _own_redis:
        try:
            redis, _gen = await _get_redis_client()
        except Exception:
            redis = None
    await _invalidate_cache(redis, worker_id)
    await _invalidate_cache(redis, platform_account_id)
    if _own_redis and _gen is not None:
        await _close_redis_client(redis, _gen)

    return {"worker_amount": worker_amount, "platform_amount": platform_amount}
