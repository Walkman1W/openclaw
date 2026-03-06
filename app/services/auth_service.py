from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Header, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.account import Account

_ALGORITHM = "HS256"
_DEFAULT_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return bcrypt hash of *password*."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT containing *data*.

    Default validity window is 24 hours unless *expires_delta* is provided.
    """
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(hours=_DEFAULT_EXPIRE_HOURS)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate *token*.  Raises HTTP 401 on any failure."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# API Key helpers
# ---------------------------------------------------------------------------

def hash_api_key(api_key: str) -> str:
    """Return SHA-256 hex digest of *api_key* (for storage)."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Return a new random API key with prefix ``oc_``."""
    return "oc_" + os.urandom(32).hex()


# ---------------------------------------------------------------------------
# FastAPI dependency functions
# ---------------------------------------------------------------------------

async def get_current_account(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Account:
    """FastAPI dependency: extract account from JWT and return ORM object."""
    payload = decode_token(token)
    account_id: str | None = payload.get("sub")
    if account_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account


def get_current_agent(current_account: Account = Depends(get_current_account)) -> Account:
    """FastAPI dependency: ensure the authenticated account is an agent."""
    if current_account.account_type != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent account required",
        )
    return current_account


def get_admin_account(
    request: Request,
    x_admin_token: str = Header(...),
) -> bool:
    """FastAPI dependency: validate X-Admin-Token header."""
    if x_admin_token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token",
        )
    return True


async def get_agent_by_api_key(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Account:
    """FastAPI dependency: authenticate an agent via X-API-Key header."""
    key_hash = hash_api_key(x_api_key)
    result = await db.execute(
        select(Account).where(
            Account.api_key_hash == key_hash,
            Account.account_type == "agent",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    if account.status == "frozen":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is frozen",
        )
    return account
