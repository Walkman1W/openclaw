"""TASK-028/029: Layer 1 LLM audit and benchmark task workers."""
from __future__ import annotations

import asyncio
import uuid

from app.celery_app import celery_app


async def _async_layer1_audit(task_id_str: str) -> dict:
    from app.database import AsyncSessionLocal
    from app.models.audit_log import AuditLog
    from app.models.task import Task
    from app.services.bootstrap import get_crayfish_account_id, get_platform_account_id
    from app.services.layer1_audit import run_layer1_audit, should_sample
    from app.services.task_service import settle_task
    from sqlalchemy import select

    task_id = uuid.UUID(task_id_str)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

        if task is None or task.status != "under_audit":
            return {"skipped": True, "reason": "task not found or not under_audit"}

        # Decide whether to sample this task for Layer 1
        if not should_sample():
            # Not sampled: proceed directly to settlement
            platform_id = await get_platform_account_id(db)
            crayfish_id = await get_crayfish_account_id(db)
            settlement = await settle_task(db, task_id, platform_id, crayfish_id)
            await db.commit()
            return {"sampled": False, "settled": True, **settlement}

        # Run Layer 1 audit
        task_data = {
            "title": task.title,
            "task_type": task.task_type,
            "input_spec": task.input_spec,
            "output_spec": task.output_spec,
            "acceptance_criteria": task.acceptance_criteria,
        }
        verdict = await run_layer1_audit(task_data, task.result_data or {})

        # Write audit log
        db.add(AuditLog(
            task_id=task.id,
            layer=1,
            auditor_id=None,
            result=verdict["result"],
            confidence=verdict["confidence"],
            checks={"reasons": verdict["reasons"]},
        ))

        platform_id = await get_platform_account_id(db)
        crayfish_id = await get_crayfish_account_id(db)

        if verdict["result"] == "pass":
            settlement = await settle_task(db, task_id, platform_id, crayfish_id)
            await db.commit()
            return {"sampled": True, "layer1_result": "pass", **settlement}
        else:
            # Layer 1 fail → move to disputed for human review
            from app.services.task_service import _write_history
            task.status = "disputed"
            await _write_history(db, task, "under_audit", "disputed",
                                 note=f"Layer 1 fail (confidence={verdict['confidence']})")
            await db.commit()
            return {"sampled": True, "layer1_result": "fail", "reasons": verdict["reasons"]}


async def _async_publish_benchmarks() -> int:
    """TASK-029: Publish benchmark tasks daily."""
    from app.database import AsyncSessionLocal
    from app.services.crayfish_service import publish_benchmark_tasks

    async with AsyncSessionLocal() as db:
        count = await publish_benchmark_tasks(db)
    return count


@celery_app.task(
    name="openclaw.layer1_audit",
    queue="celery",
    max_retries=2,
    default_retry_delay=30,
)
def layer1_audit_task(task_id_str: str) -> dict:
    """Run Layer 1 LLM audit for a sampled under_audit task."""
    try:
        return asyncio.run(_async_layer1_audit(task_id_str))
    except Exception as exc:
        raise layer1_audit_task.retry(exc=exc)


@celery_app.task(name="openclaw.publish_benchmarks", queue="celery")
def publish_benchmarks_task() -> int:
    """TASK-029: Publish daily benchmark tasks."""
    return asyncio.run(_async_publish_benchmarks())
