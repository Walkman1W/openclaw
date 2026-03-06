"""TASK-019: Celery workers for audit pipeline and claim release.

After submit, the route handler dispatches layer1_audit_task (which decides
whether to sample and then settles).  This file keeps the claim-release Beat task.
"""
from __future__ import annotations

import asyncio

from app.celery_app import celery_app


async def _async_release_expired() -> list[str]:
    from app.database import AsyncSessionLocal
    from app.services.task_service import release_expired_claims

    async with AsyncSessionLocal() as db:
        released = await release_expired_claims(db, redis=None)
        await db.commit()
    return [str(tid) for tid in released]


@celery_app.task(name="openclaw.release_expired_claims", queue="celery")
def release_expired_claims_task() -> list[str]:
    """TASK-014: Celery Beat – release timed-out claim locks every 2 minutes."""
    return asyncio.run(_async_release_expired())
