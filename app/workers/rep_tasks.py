"""TASK-024: Celery Beat task for monthly REP_SCORE decay."""
from __future__ import annotations

import asyncio

from app.celery_app import celery_app


async def _async_decay() -> int:
    from app.database import AsyncSessionLocal
    from app.services.rep_service import apply_monthly_decay

    async with AsyncSessionLocal() as db:
        count = await apply_monthly_decay(db)
        await db.commit()
    return count


@celery_app.task(name="openclaw.monthly_rep_decay", queue="celery")
def monthly_rep_decay_task() -> int:
    """Apply 2% monthly REP_SCORE decay to all agents.

    Scheduled on the 1st of each month at 03:00 UTC.
    """
    return asyncio.run(_async_decay())
