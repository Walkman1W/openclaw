"""TASK-005/030: Agent registration, profile, and dashboard routes."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.account import Account
from app.models.rep_history import RepHistory
from app.models.task import Task
from app.models.transaction import Transaction
from app.schemas.account import (
    AccountStatusResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.services.auth_service import (
    generate_api_key,
    get_agent_by_api_key,
    get_current_account,
    hash_api_key,
)
from app.services.claw_service import burn_claw, mint_claw

router = APIRouter()


@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    body: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AgentRegisterResponse:
    """Register a new AI agent account.

    Returns the plain-text API key exactly once — it is NOT stored in clear text.
    """
    plain_api_key = generate_api_key()
    api_key_hash = hash_api_key(plain_api_key)

    account = Account(
        account_type="agent",
        name=body.name,
        status="unverified",
        capability_tags=body.capability_tags,
        api_key_hash=api_key_hash,
        metadata_={},
    )
    db.add(account)
    await db.flush()  # populate account.id

    # Grant initial CLAW then deduct registration fee
    await mint_claw(
        db,
        account_id=account.id,
        amount=settings.initial_claw_agent,
        reason="initial_grant",
    )
    await burn_claw(
        db,
        account_id=account.id,
        amount=settings.agent_registration_fee,
        reason="registration_fee",
    )

    await db.commit()
    await db.refresh(account)

    # TASK-022: trigger onboarding verification tasks for new agents
    from app.workers.crayfish_tasks import assign_onboarding_tasks
    assign_onboarding_tasks.delay(str(account.id))

    return AgentRegisterResponse(
        agent_id=account.id,
        api_key=plain_api_key,
        initial_claw=settings.initial_claw_agent - settings.agent_registration_fee,
    )


@router.get("", response_model=list[AccountStatusResponse])
async def list_agents(
    tag: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[AccountStatusResponse]:
    """Public: list active agents, optionally filtered by capability tag."""
    stmt = select(Account).where(
        Account.account_type == "agent",
        Account.status == "active",
    ).order_by(Account.rep_score.desc()).limit(limit)
    if tag:
        stmt = stmt.where(Account.capability_tags.contains([tag]))
    result = await db.execute(stmt)
    return [AccountStatusResponse.model_validate(a) for a in result.scalars()]


@router.get("/{agent_id}/profile", response_model=AccountStatusResponse)
async def get_agent_profile(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AccountStatusResponse:
    """Public endpoint: return an agent's profile without sensitive data."""
    result = await db.execute(
        select(Account).where(Account.id == agent_id, Account.account_type == "agent")
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return AccountStatusResponse.model_validate(account)


@router.get("/{agent_id}/dashboard")
async def agent_dashboard(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    agent: Account = Depends(get_agent_by_api_key),
) -> dict:
    """TASK-030: Agent personal dashboard — past 30 days stats."""
    if agent.id != agent_id:
        raise HTTPException(403, "Can only view your own dashboard")

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)

    # Task counts
    total_r = await db.execute(
        select(func.count(Task.id)).where(Task.assignee_id == agent_id, Task.created_at >= cutoff)
    )
    settled_r = await db.execute(
        select(func.count(Task.id)).where(
            Task.assignee_id == agent_id,
            Task.status.in_(["settled", "settled_override"]),
            Task.updated_at >= cutoff,
        )
    )
    total_tasks = total_r.scalar() or 0
    settled_tasks = settled_r.scalar() or 0
    completion_rate = round(settled_tasks / total_tasks, 3) if total_tasks else 0.0

    # Earnings (sum of reward and deposit_refund transactions)
    earnings_r = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.account_id == agent_id,
            Transaction.tx_type == "mint",
            Transaction.reason.in_(["task_reward", "claim_deposit_refund"]),
            Transaction.created_at >= cutoff,
        )
    )
    total_earnings = int(earnings_r.scalar() or 0)

    # REP history (time series)
    rep_rows = await db.execute(
        select(RepHistory).where(
            RepHistory.account_id == agent_id,
            RepHistory.created_at >= cutoff,
        ).order_by(RepHistory.created_at.asc())
    )
    rep_series = [
        {"date": r.created_at.date().isoformat(), "delta": r.delta,
         "score_after": r.score_after, "reason": r.reason}
        for r in rep_rows.scalars()
    ]

    return {
        "agent_id": str(agent_id),
        "current_rep_score": agent.rep_score,
        "period_days": 30,
        "total_tasks": total_tasks,
        "settled_tasks": settled_tasks,
        "completion_rate": completion_rate,
        "total_earnings_claw": total_earnings,
        "rep_history": rep_series,
    }


@router.get("/{agent_id}/rep_history")
async def agent_rep_history(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    agent: Account = Depends(get_agent_by_api_key),
) -> list[dict]:
    """Return full REP_SCORE change history for authenticated agent."""
    if agent.id != agent_id:
        raise HTTPException(403, "Can only view your own REP history")
    rows = await db.execute(
        select(RepHistory).where(RepHistory.account_id == agent_id)
        .order_by(RepHistory.created_at.desc()).limit(200)
    )
    return [
        {"delta": r.delta, "score_after": r.score_after, "reason": r.reason,
         "task_id": str(r.task_id) if r.task_id else None,
         "created_at": r.created_at.isoformat()}
        for r in rows.scalars()
    ]


@router.put("/{agent_id}/tags", response_model=AccountStatusResponse)
async def update_agent_tags(
    agent_id: uuid.UUID,
    tags: list[str],
    current_account: Account = Depends(get_current_account),
    db: AsyncSession = Depends(get_db),
) -> AccountStatusResponse:
    """Update capability tags for an agent.  Only the owner may update their own tags."""
    # Authorisation: account must own this agent record
    if current_account.id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own tags",
        )

    # Validate tag count and individual tag lengths
    if not (1 <= len(tags) <= 10):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="capability_tags must contain between 1 and 10 items",
        )
    for tag in tags:
        if not (1 <= len(tag) <= 50):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Each tag must be between 1 and 50 characters, got: {tag!r}",
            )

    result = await db.execute(
        select(Account).where(Account.id == agent_id, Account.account_type == "agent")
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    account.capability_tags = tags
    await db.commit()
    await db.refresh(account)
    return AccountStatusResponse.model_validate(account)
