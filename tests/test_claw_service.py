"""Unit tests for app.services.claw_service (TASK-006).

All tests use unittest.mock — no real database or Redis connection required.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.claw_service import (
    InsufficientBalanceError,
    burn_claw,
    get_balance,
    mint_claw,
    settle_task_reward,
    transfer_claw,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(balance: int = 0):
    """Return a mock AsyncSession whose aggregate always returns *balance*."""
    db = AsyncMock()

    # mock scalar result for _compute_balance_from_db
    scalar_result = MagicMock()
    scalar_result.scalar.return_value = balance

    # mock for SELECT FOR UPDATE (returns an object with scalar_one_or_none)
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = MagicMock()

    # execute returns either the lock result or the balance result depending on call count
    call_count = {"n": 0}

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count["n"] += 1
        # First execute per operation is the FOR UPDATE lock; subsequent are aggregates
        # We use a simple counter: odd calls = lock, even calls = aggregate
        # But for simplicity, return scalar_result always — callers use .scalar()
        # For FOR UPDATE calls the caller ignores the return value.
        return scalar_result

    db.execute.side_effect = execute_side_effect
    db.flush = AsyncMock()
    db.add = MagicMock()

    return db


def _make_db_two_balances(from_balance: int, to_balance: int):
    """Return a mock DB that returns two different balances for two accounts."""
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    call_count = {"n": 0}

    async def execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        call_count["n"] += 1
        # Calls: 1=lock acct A, 2=lock acct B, 3=from balance, 4=to balance
        # We use modular logic: even scalar calls return respective balances
        if call_count["n"] == 3:
            result.scalar.return_value = from_balance
        elif call_count["n"] == 4:
            result.scalar.return_value = to_balance
        else:
            result.scalar.return_value = 0
        return result

    db.execute.side_effect = execute_side_effect
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_increases_balance():
    """mint_claw inserts a Transaction with amount=+value and correct balance_after."""
    account_id = uuid.uuid4()
    db = _make_db(balance=100)

    with patch("app.services.claw_service._get_redis_client", side_effect=Exception("no redis")):
        tx = await mint_claw(db, account_id, amount=50, reason="test_mint")

    assert tx.amount == 50
    assert tx.balance_after == 150  # 100 + 50
    assert tx.tx_type == "mint"
    db.add.assert_called_once_with(tx)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_burn_decreases_balance():
    """burn_claw inserts a Transaction with amount=-value and correct balance_after."""
    account_id = uuid.uuid4()
    db = _make_db(balance=200)

    with patch("app.services.claw_service._get_redis_client", side_effect=Exception("no redis")):
        tx = await burn_claw(db, account_id, amount=80, reason="test_burn")

    assert tx.amount == -80
    assert tx.balance_after == 120  # 200 - 80
    assert tx.tx_type == "burn"
    db.add.assert_called_once_with(tx)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_burn_insufficient_raises():
    """burn_claw raises InsufficientBalanceError when balance < amount."""
    account_id = uuid.uuid4()
    db = _make_db(balance=30)

    with patch("app.services.claw_service._get_redis_client", side_effect=Exception("no redis")):
        with pytest.raises(InsufficientBalanceError):
            await burn_claw(db, account_id, amount=100, reason="test_overdraft")


@pytest.mark.asyncio
async def test_transfer_atomic():
    """transfer_claw writes exactly two Transaction rows in the same DB session."""
    from_id = uuid.uuid4()
    to_id = uuid.uuid4()
    db = _make_db_two_balances(from_balance=300, to_balance=50)

    with patch("app.services.claw_service._get_redis_client", side_effect=Exception("no redis")):
        from_tx, to_tx = await transfer_claw(
            db,
            from_account_id=from_id,
            to_account_id=to_id,
            amount=100,
            tx_type_from="deposit",
            tx_type_to="reward",
            reason="test_transfer",
        )

    # Two add() calls — one per transaction row
    assert db.add.call_count == 2
    db.flush.assert_awaited_once()

    assert from_tx.amount == -100
    assert to_tx.amount == 100
    assert from_tx.account_id == from_id
    assert to_tx.account_id == to_id


@pytest.mark.asyncio
async def test_settle_task_reward_split():
    """settle_task_reward distributes 80% to worker and 20% to platform."""
    task_id = uuid.uuid4()
    publisher_id = uuid.uuid4()
    worker_id = uuid.uuid4()
    platform_id = uuid.uuid4()

    # Build a DB mock that handles three locks + two balance queries
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    call_count = {"n": 0}

    async def execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        call_count["n"] += 1
        # Calls 1-3: FOR UPDATE locks (return value ignored by caller)
        # Call 4: worker balance aggregate
        # Call 5: platform balance aggregate
        if call_count["n"] == 4:
            result.scalar.return_value = 0  # worker starts at 0
        elif call_count["n"] == 5:
            result.scalar.return_value = 0  # platform starts at 0
        else:
            result.scalar.return_value = 0
        return result

    db.execute.side_effect = execute_side_effect

    with patch("app.services.claw_service._get_redis_client", side_effect=Exception("no redis")):
        result = await settle_task_reward(
            db,
            task_id=task_id,
            publisher_id=publisher_id,
            worker_id=worker_id,
            platform_account_id=platform_id,
            reward_pool=1000,
        )

    assert result["worker_amount"] == 800   # 80%
    assert result["platform_amount"] == 200  # 20%
    assert result["worker_amount"] + result["platform_amount"] == 1000


@pytest.mark.asyncio
async def test_balance_negative_raises():
    """get_balance raises RuntimeError when the aggregated balance is negative."""
    account_id = uuid.uuid4()

    db = AsyncMock()
    neg_result = MagicMock()
    neg_result.scalar.return_value = -50  # data inconsistency
    db.execute.return_value = neg_result

    # Redis miss so it falls through to DB
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # cache miss
    mock_gen = AsyncMock()

    async def fake_get_redis():
        return mock_redis, mock_gen

    with patch("app.services.claw_service._get_redis_client", new=fake_get_redis):
        with pytest.raises(RuntimeError, match="Negative balance"):
            await get_balance(db, account_id)
