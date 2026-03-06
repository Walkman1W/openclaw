"""TASK-027/032/033/034/035: Admin endpoint tests."""
import pytest
from httpx import AsyncClient

_ADMIN_HEADERS = {"x-admin-token": "change-me-admin-token"}


@pytest.mark.asyncio
async def test_admin_overview_returns_metrics(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/admin/overview", headers=_ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "task_counts" in data
    assert "pending" in data["task_counts"]


@pytest.mark.asyncio
async def test_admin_overview_requires_token(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/admin/overview")
    assert resp.status_code == 422  # missing header


@pytest.mark.asyncio
async def test_admin_tokenomics(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/admin/tokenomics", headers=_ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "weeks" in data
    assert len(data["weeks"]) == 8


@pytest.mark.asyncio
async def test_admin_mint_and_burn(async_client: AsyncClient):
    # Register a human to get an account
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "minttest@example.com", "password": "password123", "name": "MintUser"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "minttest@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]
    me = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    account_id = me.json()["id"]

    # Mint
    mint_resp = await async_client.post(
        "/api/v1/admin/claw/mint",
        json={"account_id": account_id, "amount": 100, "reason": "test_bonus"},
        headers=_ADMIN_HEADERS,
    )
    assert mint_resp.status_code == 200
    assert mint_resp.json()["amount"] == 100

    # Burn
    burn_resp = await async_client.post(
        "/api/v1/admin/claw/burn",
        json={"account_id": account_id, "amount": 50, "reason": "test_penalty"},
        headers=_ADMIN_HEADERS,
    )
    assert burn_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_restrict_agent(async_client: AsyncClient):
    reg = await async_client.post(
        "/api/v1/agents/register",
        json={"name": "RestrictBot", "capability_tags": ["test"]},
    )
    agent_id = reg.json()["agent_id"]

    resp = await async_client.post(
        f"/api/v1/admin/agents/{agent_id}/restrict",
        json={"action": "freeze", "reason": "suspicious behaviour"},
        headers=_ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "frozen"


@pytest.mark.asyncio
async def test_admin_disputes_list(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/admin/disputes", headers=_ADMIN_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
