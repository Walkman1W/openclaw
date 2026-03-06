"""TASK-021/022: Celery workers for Crayfish Agent automation."""
from __future__ import annotations

import asyncio
import uuid

from app.celery_app import celery_app


async def _async_assign_onboarding(agent_id_str: str) -> int:
    from app.database import AsyncSessionLocal
    from app.services.crayfish_service import assign_verification_tasks

    async with AsyncSessionLocal() as db:
        return await assign_verification_tasks(db, uuid.UUID(agent_id_str))


async def _async_coldstart() -> int:
    from app.database import AsyncSessionLocal
    from app.services.crayfish_service import maybe_publish_coldstart_tasks

    async with AsyncSessionLocal() as db:
        return await maybe_publish_coldstart_tasks(db)


@celery_app.task(
    name="openclaw.assign_onboarding_tasks",
    queue="celery",
    max_retries=2,
    default_retry_delay=5,
)
def assign_onboarding_tasks(agent_id_str: str) -> int:
    """Assign 5 verification tasks to a newly registered agent."""
    try:
        return asyncio.run(_async_assign_onboarding(agent_id_str))
    except Exception as exc:
        raise assign_onboarding_tasks.retry(exc=exc)


@celery_app.task(
    name="openclaw.coldstart_publisher",
    queue="celery",
)
def coldstart_publisher_task() -> int:
    """Publish Level 1 tasks if pending pool is below threshold."""
    return asyncio.run(_async_coldstart())
