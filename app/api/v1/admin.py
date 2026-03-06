"""TASK-027/032/033/034/035: Admin endpoints.

All routes require X-Admin-Token header (get_admin_account dependency).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.account import Account
from app.models.task import Task
from app.models.transaction import Transaction
from app.services.auth_service import get_admin_account
from app.services.bootstrap import get_crayfish_account_id, get_platform_account_id
from app.services.claw_service import burn_claw, mint_claw
from app.services.task_service import admin_resolve_dispute

router = APIRouter()


# ---------------------------------------------------------------------------
# TASK-032: GET /admin/overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def admin_overview(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """Real-time platform health metrics (updated within 5 minutes)."""
    statuses = [
        "pending", "claimed", "in_progress", "submitted",
        "under_audit", "settled", "disputed",
    ]
    counts: dict[str, int] = {}
    for s in statuses:
        r = await db.execute(select(func.count(Task.id)).where(Task.status == s))
        counts[s] = r.scalar() or 0

    # Layer 0 pass rate (last 24 hours)
    from app.models.audit_log import AuditLog
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    total_r = await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.layer == 0, AuditLog.created_at >= cutoff)
    )
    pass_r = await db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.layer == 0, AuditLog.result == "pass", AuditLog.created_at >= cutoff
        )
    )
    total_audits = total_r.scalar() or 0
    passed_audits = pass_r.scalar() or 0
    layer0_pass_rate = round(passed_audits / total_audits, 3) if total_audits else None

    # Daily active agents (claimed or submitted tasks in last 24h)
    active_r = await db.execute(
        select(func.count(func.distinct(Task.assignee_id))).where(
            Task.updated_at >= cutoff,
            Task.assignee_id.isnot(None),
        )
    )
    daily_active_agents = active_r.scalar() or 0

    return {
        "task_counts": counts,
        "layer0_pass_rate_24h": layer0_pass_rate,
        "total_audits_24h": total_audits,
        "daily_active_agents": daily_active_agents,
    }


# ---------------------------------------------------------------------------
# TASK-035: GET /admin/tokenomics
# ---------------------------------------------------------------------------

@router.get("/tokenomics")
async def admin_tokenomics(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """CLAW supply/destroy/circulation trend over the past 8 weeks."""
    rows = []
    now = datetime.now(tz=timezone.utc)
    for week in range(8):
        week_start = now - timedelta(weeks=week + 1)
        week_end = now - timedelta(weeks=week)

        mint_r = await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.tx_type == "mint",
                Transaction.created_at >= week_start,
                Transaction.created_at < week_end,
            )
        )
        burn_r = await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.tx_type.in_(["burn", "deposit_burn"]),
                Transaction.created_at >= week_start,
                Transaction.created_at < week_end,
            )
        )
        minted = int(mint_r.scalar() or 0)
        burned = int(abs(burn_r.scalar() or 0))
        rows.append({
            "week_start": week_start.date().isoformat(),
            "week_end": week_end.date().isoformat(),
            "minted": minted,
            "burned": burned,
            "net": minted - burned,
        })

    rows.reverse()  # chronological order
    return {"weeks": rows}


# ---------------------------------------------------------------------------
# TASK-033: POST /admin/claw/mint  and  /admin/claw/burn
# ---------------------------------------------------------------------------

class ClawAdminRequest(BaseModel):
    account_id: uuid.UUID
    amount: int
    reason: str


@router.post("/claw/mint", status_code=status.HTTP_200_OK)
async def admin_mint_claw(
    body: ClawAdminRequest,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """Admin: mint CLAW to any account. Requires reason."""
    acc = await db.execute(select(Account).where(Account.id == body.account_id))
    if acc.scalar_one_or_none() is None:
        raise HTTPException(404, "Account not found")
    tx = await mint_claw(db, account_id=body.account_id, amount=body.amount,
                         reason=body.reason)
    await db.commit()
    return {"tx_id": tx.id, "amount": body.amount, "balance_after": tx.balance_after}


@router.post("/claw/burn", status_code=status.HTTP_200_OK)
async def admin_burn_claw(
    body: ClawAdminRequest,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """Admin: burn CLAW from any account. Requires reason."""
    acc = await db.execute(select(Account).where(Account.id == body.account_id))
    if acc.scalar_one_or_none() is None:
        raise HTTPException(404, "Account not found")
    from app.services.claw_service import InsufficientBalanceError
    try:
        tx = await burn_claw(db, account_id=body.account_id, amount=body.amount,
                             reason=body.reason)
        await db.commit()
        return {"tx_id": tx.id, "amount": body.amount, "balance_after": tx.balance_after}
    except InsufficientBalanceError as exc:
        raise HTTPException(402, str(exc)) from exc


# ---------------------------------------------------------------------------
# TASK-034: POST /admin/agents/{agent_id}/restrict
# ---------------------------------------------------------------------------

class RestrictRequest(BaseModel):
    action: Literal["weight_reduce", "freeze"]
    reason: str


@router.post("/agents/{agent_id}/restrict")
async def admin_restrict_agent(
    agent_id: uuid.UUID,
    body: RestrictRequest,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """Admin: reduce match weight or freeze an agent account."""
    result = await db.execute(
        select(Account).where(Account.id == agent_id, Account.account_type == "agent")
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    if body.action == "freeze":
        agent.status = "frozen"
    else:  # weight_reduce
        agent.status = "restricted"

    meta = dict(agent.metadata_ or {})
    meta.setdefault("admin_actions", []).append({
        "action": body.action,
        "reason": body.reason,
        "at": datetime.now(tz=timezone.utc).isoformat(),
    })
    agent.metadata_ = meta

    await db.commit()
    return {"agent_id": str(agent_id), "new_status": agent.status, "action": body.action}


# ---------------------------------------------------------------------------
# TASK-027: GET /admin/disputes  and  POST /admin/disputes/{task_id}/resolve
# ---------------------------------------------------------------------------

@router.get("/disputes")
async def list_disputes(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> list[dict]:
    result = await db.execute(
        select(Task).where(Task.status == "disputed")
        .order_by(Task.updated_at.asc())
        .offset(offset).limit(limit)
    )
    tasks = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "assignee_id": str(t.assignee_id) if t.assignee_id else None,
            "publisher_id": str(t.publisher_id),
            "reward_pool": t.reward_pool,
            "dispute_reason": (t.metadata_ or {}).get("dispute_reason"),
            "updated_at": t.updated_at.isoformat(),
        }
        for t in tasks
    ]


class ResolveRequest(BaseModel):
    action: Literal["settled_override", "failed"]
    note: str | None = None


@router.post("/disputes/{task_id}/resolve")
async def resolve_dispute(
    task_id: uuid.UUID,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(get_admin_account),
) -> dict:
    """Admin: manually resolve a disputed task."""
    try:
        platform_id = await get_platform_account_id(db)
        crayfish_id = await get_crayfish_account_id(db)
        result = await admin_resolve_dispute(
            db, task_id, body.action, platform_id, crayfish_id, body.note
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
