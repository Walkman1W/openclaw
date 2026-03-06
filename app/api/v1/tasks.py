"""TASK-010/011/013/015: Task publishing, recommendation, and lifecycle routes."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.task import Task
from app.models.task_status_history import TaskStatusHistory
from app.redis_client import get_redis
from app.schemas.task import TaskPublishRequest, TaskResponse
from app.services.auth_service import get_agent_by_api_key, get_current_account
from app.services.claw_service import InsufficientBalanceError, get_balance, lock_claw
from app.services.criteria_validator import CriteriaValidationError, validate_acceptance_criteria
from app.services.matching import get_recommended_tasks
from app.services.task_service import (
    claim_task,
    dispute_task,
    settle_task,
    start_task,
    submit_task,
)
from app.models.account import Account

router = APIRouter()

_IDEMPOTENCY_WINDOW = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_content_hash(body: TaskPublishRequest) -> str:
    canonical = {
        "title": body.title,
        "task_type": body.task_type,
        "task_level": body.task_level,
        "input_spec": body.input_spec,
        "output_spec": body.output_spec,
        "acceptance_criteria": body.acceptance_criteria,
        "reward_pool": body.reward_pool,
        "deadline": body.deadline.isoformat(),
        "required_tags": sorted(body.required_tags),
        "preferred_tags": sorted(body.preferred_tags),
        "min_reputation": body.min_reputation,
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _task_service_error_to_http(error_key: str) -> HTTPException:
    mapping = {
        "task_not_found": (404, "Task not found"),
        "task_already_claimed": (409, "Task is already claimed by another agent"),
        "task_not_claimable": (409, "Task is not in a claimable state"),
        "task_not_claimed": (409, "Task is not in 'claimed' state"),
        "task_not_in_progress": (409, "Task is not in 'in_progress' state"),
        "not_task_assignee": (403, "You are not assigned to this task"),
        "rep_too_low": (403, "Your REP_SCORE does not meet the task's min_reputation"),
        "agent_not_active": (403, "Your account is not active"),
        "insufficient_balance_for_deposit": (402, "Insufficient CLAW balance for claim deposit"),
    }
    for key, (code, msg) in mapping.items():
        if error_key.startswith(key):
            return HTTPException(status_code=code, detail=msg)
    return HTTPException(status_code=400, detail=error_key)


# ---------------------------------------------------------------------------
# GET /tasks/recommended  (TASK-011)
# Define BEFORE /{task_id} to avoid routing conflict
# ---------------------------------------------------------------------------


@router.get("/recommended", response_model=list[dict])
async def get_recommended(
    limit: int = Query(default=20, ge=1, le=100),
    agent: Account = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return tasks recommended for the authenticated agent, sorted by match score."""
    scored = await get_recommended_tasks(db, agent, limit=limit)
    return [
        {**TaskResponse.model_validate(task).model_dump(), "match_score": round(score, 4)}
        for task, score in scored
    ]


# ---------------------------------------------------------------------------
# GET /tasks  — public marketplace listing
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    """Public task listing for marketplace browsing."""
    stmt = select(Task).order_by(Task.created_at.desc())
    if status:
        stmt = stmt.where(Task.status == status)
    if tag:
        stmt = stmt.where(Task.required_tags.contains([tag]))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [TaskResponse.model_validate(t) for t in result.scalars()]


# ---------------------------------------------------------------------------
# POST /tasks  (TASK-010)
# ---------------------------------------------------------------------------


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def publish_task(
    body: TaskPublishRequest,
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Publish a new task contract.

    Validates acceptance_criteria (400), checks balance (402),
    idempotency guard within 5 min (409), and locks reward_pool into escrow.
    """
    try:
        validate_acceptance_criteria(body.acceptance_criteria)
    except CriteriaValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid acceptance_criteria", "errors": exc.errors},
        ) from exc

    content_hash = _compute_content_hash(body)

    window_start = datetime.now(tz=timezone.utc) - _IDEMPOTENCY_WINDOW
    dup = await db.execute(
        select(Task).where(
            Task.publisher_id == current_account.id,
            Task.content_hash == content_hash,
            Task.created_at >= window_start,
        )
    )
    existing = dup.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Duplicate task published within the last 5 minutes",
                "existing_task_id": str(existing.id),
            },
        )

    balance = await get_balance(db, current_account.id)
    if balance < body.reward_pool:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient CLAW: have {balance}, need {body.reward_pool}",
        )

    # Extract honeypot answers from criteria (store privately in metadata)
    honeypot_answers: dict[str, Any] = {}
    for assertion in body.acceptance_criteria:
        if assertion.get("type") == "honeypot_exact_match":
            field = assertion.get("field", "")
            if "expected_value" in assertion:
                honeypot_answers[field] = assertion.pop("expected_value")

    task = Task(
        publisher_id=current_account.id,
        title=body.title,
        task_type=body.task_type,
        task_level=body.task_level,
        input_spec=body.input_spec,
        output_spec=body.output_spec,
        acceptance_criteria=body.acceptance_criteria,
        reward_pool=body.reward_pool,
        deposit_held=body.reward_pool,
        deadline=body.deadline,
        required_tags=body.required_tags,
        preferred_tags=body.preferred_tags,
        min_reputation=body.min_reputation,
        status="pending",
        content_hash=content_hash,
        metadata_={"honeypot_answers": honeypot_answers} if honeypot_answers else None,
    )
    db.add(task)
    await db.flush()

    await lock_claw(
        db,
        account_id=current_account.id,
        amount=body.reward_pool,
        task_id=task.id,
        reason="task_reward_escrow",
    )

    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=None,
            to_status="pending",
            triggered_by=current_account.id,
            note="task published",
        )
    )
    await db.commit()
    await db.refresh(task)
    return TaskResponse.model_validate(task)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}
# ---------------------------------------------------------------------------


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/claim  (TASK-013)
# ---------------------------------------------------------------------------


@router.post("/{task_id}/claim", response_model=TaskResponse)
async def claim_task_route(
    task_id: uuid.UUID,
    agent: Account = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> TaskResponse:
    """Claim a pending task (deducts 10% deposit, sets 30-min lock)."""
    try:
        task = await claim_task(db, task_id, agent, redis)
        await db.commit()
        await db.refresh(task)
        return TaskResponse.model_validate(task)
    except ValueError as exc:
        raise _task_service_error_to_http(str(exc)) from exc
    except InsufficientBalanceError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/start  (TASK-015)
# ---------------------------------------------------------------------------


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task_route(
    task_id: uuid.UUID,
    agent: Account = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Transition task from claimed → in_progress."""
    try:
        task = await start_task(db, task_id, agent)
        await db.commit()
        await db.refresh(task)
        return TaskResponse.model_validate(task)
    except ValueError as exc:
        raise _task_service_error_to_http(str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/submit  (TASK-015 + TASK-018)
# ---------------------------------------------------------------------------


class SubmitBody(BaseModel):
    result_data: dict[str, Any]


@router.post("/{task_id}/submit")
async def submit_task_route(
    task_id: uuid.UUID,
    body: SubmitBody,
    agent: Account = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit task result and run Layer 0 audit synchronously.

    Returns audit verdict within 5 seconds (P95 target).
    If Layer 0 passes, triggers background settlement task.
    """
    try:
        audit_result = await submit_task(db, task_id, agent, body.result_data)
        await db.commit()
    except ValueError as exc:
        raise _task_service_error_to_http(str(exc)) from exc

    # Trigger Layer 1 audit pipeline (TASK-028: samples 15%, then settles)
    if audit_result["passed"]:
        from app.workers.layer1_tasks import layer1_audit_task
        layer1_audit_task.delay(str(task_id))

    return audit_result


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/audit-result
# ---------------------------------------------------------------------------


@router.get("/{task_id}/audit-result")
async def get_audit_result(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the most recent Layer 0 audit result for a task."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.task_id == task_id, AuditLog.layer == 0)
        .order_by(AuditLog.created_at.desc())
    )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail="No audit result found for this task")
    return {
        "task_id": str(task_id),
        "layer": log.layer,
        "result": log.result,
        "checks": log.checks,
        "created_at": log.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/dispute  (TASK-026)
# ---------------------------------------------------------------------------


class DisputeBody(BaseModel):
    reason: str


@router.post("/{task_id}/dispute")
async def dispute_task_route(
    task_id: uuid.UUID,
    body: DisputeBody,
    agent: Account = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Agent raises a dispute on a failed or under_audit task (24-hour window)."""
    try:
        task = await dispute_task(db, task_id, agent, body.reason)
        await db.commit()
        return {"task_id": str(task.id), "status": task.status}
    except ValueError as exc:
        raise _task_service_error_to_http(str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/history
# ---------------------------------------------------------------------------


@router.get("/{task_id}/history")
async def get_task_history(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return full state transition history for a task."""
    result = await db.execute(
        select(TaskStatusHistory)
        .where(TaskStatusHistory.task_id == task_id)
        .order_by(TaskStatusHistory.created_at.asc())
    )
    rows = result.scalars().all()
    return [
        {
            "from_status": r.from_status,
            "to_status": r.to_status,
            "triggered_by": str(r.triggered_by) if r.triggered_by else None,
            "note": r.note,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
