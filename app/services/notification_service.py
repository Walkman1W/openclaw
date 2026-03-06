"""TASK-036: Notification service.

Write-side helpers; route handlers call push_notification().
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def push_notification(
    db: AsyncSession,
    account_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a notification record for *account_id*.

    Caller owns the DB transaction.  Never raises; silently skips on error.
    """
    try:
        db.add(Notification(
            account_id=account_id,
            event_type=event_type,
            payload=payload or {},
        ))
    except Exception:
        pass  # notifications are best-effort
