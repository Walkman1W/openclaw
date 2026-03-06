"""TASK-013/014/015/016/023/026: Task state machine service.

All state transitions go through this module.  Route handlers call these
functions; none of them commit — the caller owns the transaction boundary.

State flow:
  pending → claimed → in_progress → submitted → under_audit → settled
                                                             ↘ disputed
  Any state → failed (Layer 0 fail or admin action)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.task import Task
from app.models.task_status_history import TaskStatusHistory
from app.services.claw_service import (
    InsufficientBalanceError,
    burn_claw,
    get_balance,
    lock_claw,
    mint_claw,
    unlock_claw,
)
from app.services.layer0_audit import run_layer0_audit
from app.services.notification_service import push_notification
from app.services.rep_service import apply_rep_delta, max_reward_for_rep

_CLAIM_TTL_SECONDS = 1800  # 30 minutes
_CLAIM_REDIS_KEY = "task:claim:{task_id}"
_DEPOSIT_RATE = 0.10
_MIN_DEPOSIT = 5

# Settlement split — TASK-016
_WORKER_SHARE = 0.78
_AUDIT_SHARE = 0.08   # → crayfish account
_PLATFORM_SHARE = 0.14  # 10% fee + 4% arbitration reserve


def _deposit_amount(reward_pool: int) -> int:
    return max(_MIN_DEPOSIT, int(reward_pool * _DEPOSIT_RATE))


async def _write_history(
    db: AsyncSession,
    task: Task,
    from_status: str | None,
    to_status: str,
    triggered_by: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    db.add(TaskStatusHistory(
        task_id=task.id,
        from_status=from_status,
        to_status=to_status,
        triggered_by=triggered_by,
        note=note,
    ))


async def _lock_task_row(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    result = await db.execute(
        select(Task).where(Task.id == task_id).with_for_update()
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# TASK-013: Claim
# ---------------------------------------------------------------------------

async def claim_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    agent: Account,
    redis,
) -> Task:
    """Claim a pending task.  Acquires Redis SETNX lock + 10% deposit."""
    redis_key = _CLAIM_REDIS_KEY.format(task_id=task_id)
    acquired = await redis.set(redis_key, str(agent.id), nx=True, ex=_CLAIM_TTL_SECONDS)
    if not acquired:
        raise ValueError("task_already_claimed")

    task = await _lock_task_row(db, task_id)
    if task is None:
        await redis.delete(redis_key)
        raise ValueError("task_not_found")

    if task.status != "pending":
        await redis.delete(redis_key)
        raise ValueError(f"task_not_claimable:{task.status}")

    if agent.rep_score < task.min_reputation:
        await redis.delete(redis_key)
        raise ValueError(f"rep_too_low:{agent.rep_score}<{task.min_reputation}")

    if agent.status != "active":
        await redis.delete(redis_key)
        raise ValueError(f"agent_not_active:{agent.status}")

    # TASK-025: reward ceiling check
    ceiling = max_reward_for_rep(agent.rep_score)
    if task.reward_pool > ceiling:
        await redis.delete(redis_key)
        raise ValueError(f"reward_exceeds_rep_ceiling:{task.reward_pool}>{ceiling}")

    deposit = _deposit_amount(task.reward_pool)
    try:
        await lock_claw(db, account_id=agent.id, amount=deposit, task_id=task.id,
                        reason="claim_deposit", redis=redis)
    except InsufficientBalanceError:
        await redis.delete(redis_key)
        raise ValueError("insufficient_balance_for_deposit")

    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_CLAIM_TTL_SECONDS)
    task.status = "claimed"
    task.assignee_id = agent.id
    task.claim_expires_at = expires_at
    task.deposit_held = deposit

    await _write_history(db, task, "pending", "claimed", triggered_by=agent.id)
    return task


# ---------------------------------------------------------------------------
# TASK-014: Auto-release expired claims
# ---------------------------------------------------------------------------

async def release_expired_claims(db: AsyncSession, redis=None) -> list[uuid.UUID]:
    """Release all timed-out claims. Returns list of released task IDs."""
    now = datetime.now(tz=timezone.utc)
    result = await db.execute(
        select(Task).where(
            Task.status == "claimed",
            Task.claim_expires_at <= now,
        ).with_for_update(skip_locked=True)
    )
    tasks = result.scalars().all()
    released: list[uuid.UUID] = []

    for task in tasks:
        if task.assignee_id and task.deposit_held > 0:
            await unlock_claw(db, account_id=task.assignee_id, amount=task.deposit_held,
                              task_id=task.id, reason="claim_timeout_refund", redis=redis)

        task.status = "pending"
        task.assignee_id = None
        task.claim_expires_at = None
        task.deposit_held = 0

        await _write_history(db, task, "claimed", "pending", note="claim expired – auto-released")

        if redis is not None:
            await redis.delete(_CLAIM_REDIS_KEY.format(task_id=task.id))

        released.append(task.id)

    return released


# ---------------------------------------------------------------------------
# TASK-015: Start
# ---------------------------------------------------------------------------

async def start_task(db: AsyncSession, task_id: uuid.UUID, agent: Account) -> Task:
    task = await _lock_task_row(db, task_id)
    if task is None:
        raise ValueError("task_not_found")
    if task.status != "claimed":
        raise ValueError(f"task_not_claimed:{task.status}")
    if task.assignee_id != agent.id:
        raise ValueError("not_task_assignee")

    task.status = "in_progress"
    task.claim_expires_at = None
    await _write_history(db, task, "claimed", "in_progress", triggered_by=agent.id)
    return task


# ---------------------------------------------------------------------------
# TASK-015 + TASK-018: Submit + Layer 0 audit
# ---------------------------------------------------------------------------

async def submit_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    agent: Account,
    result_data: dict[str, Any],
) -> dict[str, Any]:
    """Submit, run Layer 0 audit synchronously, update status.

    Pass → task becomes 'under_audit', settle Celery task queued.
    Fail → task becomes 'failed', deposit burned, REP docked.
    """
    task = await _lock_task_row(db, task_id)
    if task is None:
        raise ValueError("task_not_found")
    if task.status != "in_progress":
        raise ValueError(f"task_not_in_progress:{task.status}")
    if task.assignee_id != agent.id:
        raise ValueError("not_task_assignee")

    task.result_data = result_data
    task.status = "submitted"
    await _write_history(db, task, "in_progress", "submitted", triggered_by=agent.id)

    honeypot_answers: dict | None = (task.metadata_ or {}).get("honeypot_answers")
    audit_result = run_layer0_audit(
        acceptance_criteria=task.acceptance_criteria,
        result_data=result_data,
        honeypot_answers=honeypot_answers,
    )

    db.add(AuditLog(
        task_id=task.id,
        layer=0,
        auditor_id=None,
        result="pass" if audit_result.passed else "fail",
        confidence=None,
        checks=audit_result.as_dict(),
    ))

    if audit_result.passed:
        task.status = "under_audit"
        await _write_history(db, task, "submitted", "under_audit", note="Layer 0 passed")
    else:
        task.status = "failed"
        await _write_history(db, task, "submitted", "failed", note="Layer 0 failed")

        if task.assignee_id and task.deposit_held > 0:
            await burn_claw(db, account_id=task.assignee_id, amount=task.deposit_held,
                            reason="layer0_fail_deposit_burn", task_id=task.id)
            task.deposit_held = 0

        # TASK-023: dock REP for failed audit
        await db.execute(select(Account).where(Account.id == agent.id).with_for_update())
        await apply_rep_delta(db, agent.id, "failed_layer0", task_id=task.id)

        # Notify publisher
        await push_notification(db, task.publisher_id, "task_failed", {
            "task_id": str(task.id), "reason": "Layer 0 audit failed"
        })

    return {
        "passed": audit_result.passed,
        "checks": audit_result.as_dict()["checks"],
        "task_status": task.status,
    }


# ---------------------------------------------------------------------------
# TASK-016: Settlement (78/8/14 split)
# ---------------------------------------------------------------------------

async def settle_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    platform_account_id: uuid.UUID,
    crayfish_account_id: uuid.UUID,
) -> dict[str, Any]:
    """Distribute reward_pool: 78% worker, 8% crayfish (audit), 14% platform.

    Also refunds agent deposit and updates REP_SCORE.
    """
    task = await _lock_task_row(db, task_id)
    if task is None:
        raise ValueError("task_not_found")
    if task.status == "settled":
        return {"noop": True}
    if task.status not in ("under_audit", "submitted"):
        raise ValueError(f"task_not_settleable:{task.status}")
    if task.assignee_id is None:
        raise ValueError("task_has_no_assignee")

    rp = task.reward_pool
    worker_amount = int(rp * _WORKER_SHARE)
    audit_amount = int(rp * _AUDIT_SHARE)
    platform_amount = rp - worker_amount - audit_amount  # absorbs rounding

    # Lock accounts in deterministic order
    ids_sorted = sorted(
        [task.assignee_id, platform_account_id, crayfish_account_id],
        key=lambda u: u.bytes,
    )
    for acct_id in ids_sorted:
        await db.execute(select(Account).where(Account.id == acct_id).with_for_update())

    await mint_claw(db, account_id=task.assignee_id, amount=worker_amount,
                    reason="task_reward", task_id=task.id)
    await mint_claw(db, account_id=crayfish_account_id, amount=audit_amount,
                    reason="audit_reward", task_id=task.id)
    await mint_claw(db, account_id=platform_account_id, amount=platform_amount,
                    reason="platform_fee", task_id=task.id)

    # Refund agent deposit
    deposit_refund = task.deposit_held
    if deposit_refund > 0:
        await mint_claw(db, account_id=task.assignee_id, amount=deposit_refund,
                        reason="claim_deposit_refund", task_id=task.id)

    task.status = "settled"
    await _write_history(db, task, "under_audit", "settled", note="auto-settled")

    # TASK-023: award REP for successful settlement
    await apply_rep_delta(db, task.assignee_id, "settled", task_id=task.id)

    # Notify publisher
    await push_notification(db, task.publisher_id, "task_settled", {
        "task_id": str(task.id), "worker_amount": worker_amount,
    })

    return {
        "worker_amount": worker_amount,
        "audit_amount": audit_amount,
        "platform_amount": platform_amount,
        "deposit_refunded": deposit_refund,
    }


# ---------------------------------------------------------------------------
# TASK-026: Dispute
# ---------------------------------------------------------------------------

async def dispute_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    agent: Account,
    reason: str,
) -> Task:
    """Agent raises a dispute on a failed/under_audit task (24h window)."""
    task = await _lock_task_row(db, task_id)
    if task is None:
        raise ValueError("task_not_found")

    if task.status not in ("failed", "under_audit"):
        raise ValueError(f"task_not_disputable:{task.status}")

    if task.assignee_id != agent.id:
        raise ValueError("not_task_assignee")

    # 24-hour window check (from last status transition in task updated_at)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    if task.updated_at.replace(tzinfo=timezone.utc) < cutoff:
        raise ValueError("dispute_window_expired")

    prev_status = task.status
    task.status = "disputed"
    metadata = dict(task.metadata_ or {})
    metadata["dispute_reason"] = reason[:500]
    task.metadata_ = metadata

    await _write_history(db, task, prev_status, "disputed",
                         triggered_by=agent.id, note=reason[:200])

    # Notify publisher
    await push_notification(db, task.publisher_id, "task_disputed", {
        "task_id": str(task.id), "reason": reason[:200],
    })

    return task


# ---------------------------------------------------------------------------
# TASK-027: Admin manual resolution
# ---------------------------------------------------------------------------

async def admin_resolve_dispute(
    db: AsyncSession,
    task_id: uuid.UUID,
    action: str,  # 'settled_override' | 'failed'
    platform_account_id: uuid.UUID,
    crayfish_account_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """Admin resolves a disputed task.

    action='settled_override' → pay worker as normal settlement
    action='failed' → burn remaining, dock agent REP
    """
    if action not in ("settled_override", "failed"):
        raise ValueError(f"invalid_action:{action}")

    task = await _lock_task_row(db, task_id)
    if task is None:
        raise ValueError("task_not_found")
    if task.status != "disputed":
        raise ValueError(f"task_not_disputed:{task.status}")
    if task.assignee_id is None:
        raise ValueError("task_has_no_assignee")

    if action == "settled_override":
        rp = task.reward_pool
        worker_amount = int(rp * _WORKER_SHARE)
        audit_amount = int(rp * _AUDIT_SHARE)
        platform_amount = rp - worker_amount - audit_amount

        await mint_claw(db, account_id=task.assignee_id, amount=worker_amount,
                        reason="task_reward", task_id=task.id)
        await mint_claw(db, account_id=crayfish_account_id, amount=audit_amount,
                        reason="audit_reward", task_id=task.id)
        await mint_claw(db, account_id=platform_account_id, amount=platform_amount,
                        reason="platform_fee", task_id=task.id)
        if task.deposit_held > 0:
            await mint_claw(db, account_id=task.assignee_id, amount=task.deposit_held,
                            reason="claim_deposit_refund", task_id=task.id)

        task.status = "settled_override"
        await _write_history(db, task, "disputed", "settled_override",
                             note=note or "admin: dispute resolved in agent's favour")
        await apply_rep_delta(db, task.assignee_id, "settled", task_id=task.id)
        return {"action": "settled_override", "worker_amount": worker_amount}

    else:  # failed
        if task.deposit_held > 0:
            await burn_claw(db, account_id=task.assignee_id, amount=task.deposit_held,
                            reason="layer0_fail_deposit_burn", task_id=task.id)
            task.deposit_held = 0

        task.status = "failed"
        await _write_history(db, task, "disputed", "failed",
                             note=note or "admin: dispute resolved against agent")
        await apply_rep_delta(db, task.assignee_id, "disputed_lost", task_id=task.id)

        await push_notification(db, task.publisher_id, "dispute_resolved", {
            "task_id": str(task.id), "outcome": "failed",
        })
        return {"action": "failed"}
