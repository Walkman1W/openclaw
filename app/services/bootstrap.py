"""Platform system account bootstrap.

Creates the platform treasury and crayfish accounts on startup if they don't
exist.  Both are looked up by their reserved names; UUIDs are dynamic.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction

_PLATFORM_NAME = "__platform__"
_CRAYFISH_NAME = "__crayfish__"

# Module-level cache populated after first DB lookup
_platform_id: uuid.UUID | None = None
_crayfish_id: uuid.UUID | None = None


async def _get_or_create(
    db: AsyncSession,
    account_type: str,
    name: str,
    initial_claw: int = 0,
) -> Account:
    result = await db.execute(
        select(Account).where(Account.account_type == account_type, Account.name == name)
    )
    account = result.scalar_one_or_none()
    if account is not None:
        return account

    account = Account(
        account_type=account_type,
        name=name,
        status="active",
        capability_tags=[],
        metadata_={},
    )
    db.add(account)
    await db.flush()

    if initial_claw > 0:
        balance = 0
        tx = Transaction(
            account_id=account.id,
            amount=initial_claw,
            balance_after=initial_claw,
            tx_type="mint",
            reason="system_bootstrap",
        )
        db.add(tx)

    return account


async def ensure_system_accounts(db: AsyncSession) -> None:
    """Idempotently create platform treasury and crayfish accounts."""
    global _platform_id, _crayfish_id

    platform = await _get_or_create(db, "admin", _PLATFORM_NAME, initial_claw=0)
    crayfish = await _get_or_create(db, "crayfish", _CRAYFISH_NAME, initial_claw=10_000)

    await db.commit()

    _platform_id = platform.id
    _crayfish_id = crayfish.id


async def get_platform_account_id(db: AsyncSession) -> uuid.UUID:
    """Return the platform treasury account UUID, creating it if necessary."""
    global _platform_id
    if _platform_id is not None:
        return _platform_id
    result = await db.execute(
        select(Account).where(
            Account.account_type == "admin", Account.name == _PLATFORM_NAME
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise RuntimeError("Platform account not initialised – call ensure_system_accounts() first")
    _platform_id = account.id
    return _platform_id


async def get_crayfish_account_id(db: AsyncSession) -> uuid.UUID:
    """Return the crayfish agent account UUID."""
    global _crayfish_id
    if _crayfish_id is not None:
        return _crayfish_id
    result = await db.execute(
        select(Account).where(
            Account.account_type == "crayfish", Account.name == _CRAYFISH_NAME
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise RuntimeError("Crayfish account not initialised – call ensure_system_accounts() first")
    _crayfish_id = account.id
    return _crayfish_id
