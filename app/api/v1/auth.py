"""TASK-004: Human account authentication routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.account import Account
from app.schemas.account import (
    AccountStatusResponse,
    HumanLoginRequest,
    HumanRegisterRequest,
    TokenResponse,
)
from app.services.auth_service import (
    create_access_token,
    get_current_account,
    hash_password,
    verify_password,
)
from app.services.claw_service import mint_claw

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_human(
    body: HumanRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Register a new human account and return an access token."""
    # Check for duplicate email
    result = await db.execute(select(Account).where(Account.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    account = Account(
        account_type="human",
        name=body.name,
        email=body.email,
        status="active",
        capability_tags=[],
        metadata_={"password_hash": hash_password(body.password)},
    )
    db.add(account)
    await db.flush()  # populate account.id before mint_claw

    await mint_claw(
        db,
        account_id=account.id,
        amount=settings.initial_claw_human,
        reason="initial_grant",
    )

    await db.commit()
    await db.refresh(account)

    token = create_access_token({"sub": str(account.id)})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login_human(
    body: HumanLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate a human account and return an access token."""
    result = await db.execute(select(Account).where(Account.email == body.email))
    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    password_hash = (account.metadata_ or {}).get("password_hash", "")
    if not verify_password(body.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if account.status == "frozen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is frozen",
        )

    token = create_access_token({"sub": str(account.id)})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=AccountStatusResponse)
async def get_me(
    current_account: Account = Depends(get_current_account),
) -> AccountStatusResponse:
    """Return the authenticated account's public profile."""
    return AccountStatusResponse.model_validate(current_account)
