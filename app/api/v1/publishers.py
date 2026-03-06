"""TASK-031: Publisher dashboard endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import Task
from app.models.task_status_history import TaskStatusHistory
from app.services.auth_service import get_current_account
from app.models.account import Account

router = APIRouter()


@router.get("/{publisher_id}/tasks")
async def list_publisher_tasks(
    publisher_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_account: Account = Depends(get_current_account),
) -> list[dict]:
    if current_account.id != publisher_id and current_account.account_type != "admin":
        raise HTTPException(403, "Cannot view another publisher's tasks")

    result = await db.execute(
        select(Task).where(Task.publisher_id == publisher_id)
        .order_by(Task.created_at.desc())
        .offset(offset).limit(limit)
    )
    tasks = result.scalars().all()
    return [
        {
            "id": str(t.id), "title": t.title, "status": t.status,
            "reward_pool": t.reward_pool, "created_at": t.created_at.isoformat(),
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        }
        for t in tasks
    ]


@router.get("/{publisher_id}/stats")
async def publisher_stats(
    publisher_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_account: Account = Depends(get_current_account),
) -> dict:
    """TASK-031: Publisher summary statistics."""
    if current_account.id != publisher_id and current_account.account_type != "admin":
        raise HTTPException(403, "Cannot view another publisher's stats")

    # Total published
    total_r = await db.execute(
        select(func.count(Task.id)).where(Task.publisher_id == publisher_id)
    )
    total = total_r.scalar() or 0

    # Settlement rate
    settled_r = await db.execute(
        select(func.count(Task.id)).where(
            Task.publisher_id == publisher_id,
            Task.status.in_(["settled", "settled_override"]),
        )
    )
    settled = settled_r.scalar() or 0
    settlement_rate = round(settled / total, 3) if total else 0.0

    # Average time from submitted → settled (in seconds)
    # Approximated from task_status_history
    avg_audit_duration: float | None = None
    sub_rows = await db.execute(
        select(TaskStatusHistory).where(
            TaskStatusHistory.to_status == "submitted",
            TaskStatusHistory.task_id.in_(
                select(Task.id).where(Task.publisher_id == publisher_id)
            ),
        )
    )
    settled_rows = await db.execute(
        select(TaskStatusHistory).where(
            TaskStatusHistory.to_status.in_(["settled", "settled_override"]),
            TaskStatusHistory.task_id.in_(
                select(Task.id).where(Task.publisher_id == publisher_id)
            ),
        )
    )
    sub_map = {r.task_id: r.created_at for r in sub_rows.scalars()}
    settled_map = {r.task_id: r.created_at for r in settled_rows.scalars()}
    durations = []
    for tid, settle_ts in settled_map.items():
        sub_ts = sub_map.get(tid)
        if sub_ts:
            durations.append((settle_ts - sub_ts).total_seconds())
    if durations:
        avg_audit_duration = round(sum(durations) / len(durations), 1)

    return {
        "publisher_id": str(publisher_id),
        "total_published": total,
        "total_settled": settled,
        "settlement_rate": settlement_rate,
        "avg_audit_duration_seconds": avg_audit_duration,
    }
