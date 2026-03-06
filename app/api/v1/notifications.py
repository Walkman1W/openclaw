"""TASK-036: Notification routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.account import Account
from app.models.notification import Notification
from app.services.auth_service import get_current_account

router = APIRouter()


@router.get("")
async def list_notifications(
    unread_only: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return notification events for the authenticated account."""
    stmt = select(Notification).where(Notification.account_id == current_account.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    return [
        {
            "id": str(n.id),
            "event_type": n.event_type,
            "payload": n.payload,
            "read": n.read_at is not None,
            "created_at": n.created_at.isoformat(),
        }
        for n in result.scalars()
    ]


@router.post("/mark-read")
async def mark_notifications_read(
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark all unread notifications as read for the authenticated account."""
    result = await db.execute(
        select(Notification).where(
            Notification.account_id == current_account.id,
            Notification.read_at.is_(None),
        )
    )
    now = datetime.now(tz=timezone.utc)
    count = 0
    for n in result.scalars():
        n.read_at = now
        count += 1
    await db.commit()
    return {"marked_read": count}
