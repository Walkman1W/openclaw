"""TASK-023/024/025: REP_SCORE rules, decay, and reward ceiling.

All mutations must run inside the caller's DB transaction.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.rep_history import RepHistory

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# REP delta constants
# ---------------------------------------------------------------------------
_DELTA = {
    "settled": +5,
    "failed_layer0": -15,
    "disputed_lost": -25,
}

# ---------------------------------------------------------------------------
# Reward ceiling per REP tier (TASK-025)
# ---------------------------------------------------------------------------
_REP_CEILINGS = [
    (0, 100, 50),       # rep 0–99 → max 50 CLAW
    (100, 500, 500),    # rep 100–499 → max 500 CLAW
    (500, 2000, 5000),  # rep 500–1999 → max 5000 CLAW
]
_DEFAULT_CEILING = 50_000  # rep ≥ 2000


def max_reward_for_rep(rep_score: int) -> int:
    """Return the maximum reward_pool an agent with *rep_score* may claim."""
    for lo, hi, ceiling in _REP_CEILINGS:
        if lo <= rep_score < hi:
            return ceiling
    return _DEFAULT_CEILING


# ---------------------------------------------------------------------------
# REP_SCORE mutation
# ---------------------------------------------------------------------------


async def apply_rep_delta(
    db: AsyncSession,
    account_id: uuid.UUID,
    reason: str,
    task_id: uuid.UUID | None = None,
    note: str | None = None,
) -> int:
    """Apply a pre-defined delta to account's rep_score.

    Returns the new rep_score.
    The account row must already be locked by the caller (SELECT FOR UPDATE).
    """
    delta = _DELTA.get(reason, 0)
    if delta == 0:
        return 0  # no-op

    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        return 0

    new_score = max(0, account.rep_score + delta)  # REP_SCORE >= 0
    account.rep_score = new_score

    db.add(RepHistory(
        account_id=account_id,
        delta=delta,
        score_after=new_score,
        reason=reason,
        task_id=task_id,
        note=note,
    ))
    return new_score


# ---------------------------------------------------------------------------
# Monthly 2% decay (TASK-024) — called by Celery Beat
# ---------------------------------------------------------------------------


async def apply_monthly_decay(db: AsyncSession) -> int:
    """Decay all agents' REP_SCORE by 2% (floor), executed in batch.

    Returns the number of accounts updated.
    Off-peak operation; must not be called during peak hours.
    """
    result = await db.execute(
        select(Account).where(
            Account.account_type == "agent",
            Account.rep_score > 0,
        )
    )
    agents = result.scalars().all()

    updated = 0
    for agent in agents:
        old_score = agent.rep_score
        new_score = max(0, int(old_score * 0.98))  # 2% decay, floor
        if new_score == old_score:
            continue
        delta = new_score - old_score
        agent.rep_score = new_score
        db.add(RepHistory(
            account_id=agent.id,
            delta=delta,
            score_after=new_score,
            reason="monthly_decay",
        ))
        updated += 1

    return updated
