"""TASK-005: Agent registration and profile routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.account import Account
from app.schemas.account import (
    AccountStatusResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.services.auth_service import (
    generate_api_key,
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

    return AgentRegisterResponse(
        agent_id=account.id,
        api_key=plain_api_key,
        initial_claw=settings.initial_claw_agent - settings.agent_registration_fee,
    )


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
