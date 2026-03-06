"""TASK-010 tests: task publishing endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE_DEADLINE = "2099-01-01T00:00:00Z"

_VALID_CRITERIA = [{"type": "coverage_rate", "threshold": 0.90}]

_VALID_TASK = {
    "title": "Extract product names from HTML",
    "task_type": "data_extraction",
    "task_level": 1,
    "input_spec": {"format": "html", "source": "e-commerce page"},
    "output_spec": {"format": "json", "fields": ["name", "price"]},
    "acceptance_criteria": _VALID_CRITERIA,
    "reward_pool": 20,
    "deadline": _FUTURE_DEADLINE,
    "required_tags": ["data-extraction"],
    "min_reputation": 0,
}


async def _register_and_login(client: AsyncClient, email: str = "pub@example.com") -> str:
    """Register a human account and return a Bearer token."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "name": "Publisher"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_task_success(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub1@example.com")
    resp = await async_client.post(
        "/api/v1/tasks",
        json=_VALID_TASK,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "pending"
    assert data["reward_pool"] == 20
    assert "id" in data
    assert "content_hash" in data


@pytest.mark.asyncio
async def test_get_task_by_id(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub2@example.com")
    create_resp = await async_client.post(
        "/api/v1/tasks",
        json=_VALID_TASK,
        headers={"Authorization": f"Bearer {token}"},
    )
    task_id = create_resp.json()["id"]

    get_resp = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == task_id


@pytest.mark.asyncio
async def test_publish_deducts_balance(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub3@example.com")

    before = (
        await async_client.get(
            "/api/v1/claw/balance", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()["balance"]

    await async_client.post(
        "/api/v1/tasks",
        json={**_VALID_TASK, "reward_pool": 50},
        headers={"Authorization": f"Bearer {token}"},
    )

    after = (
        await async_client.get(
            "/api/v1/claw/balance", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()["balance"]

    assert after == before - 50


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_acceptance_criteria_returns_400(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub4@example.com")
    bad_task = {**_VALID_TASK, "acceptance_criteria": [{"type": "subjective_quality"}]}
    resp = await async_client.post(
        "/api/v1/tasks",
        json=bad_task,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "errors" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_past_deadline_returns_422(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub5@example.com")
    bad_task = {**_VALID_TASK, "deadline": "2000-01-01T00:00:00Z"}
    resp = await async_client.post(
        "/api/v1/tasks",
        json=bad_task,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_insufficient_balance_returns_402(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub6@example.com")
    # Initial balance for human is 500; request more than that
    big_task = {**_VALID_TASK, "reward_pool": 99999}
    resp = await async_client.post(
        "/api/v1/tasks",
        json=big_task,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


@pytest.mark.asyncio
async def test_idempotency_within_5_minutes_returns_409(async_client: AsyncClient):
    token = await _register_and_login(async_client, "pub7@example.com")
    # First publish succeeds
    r1 = await async_client.post(
        "/api/v1/tasks",
        json=_VALID_TASK,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 201
    # Identical publish within 5 min → conflict
    r2 = await async_client.post(
        "/api/v1/tasks",
        json=_VALID_TASK,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 409
    assert "existing_task_id" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_unauthenticated_publish_returns_401(async_client: AsyncClient):
    resp = await async_client.post("/api/v1/tasks", json=_VALID_TASK)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_task_level_3_rejected(async_client: AsyncClient):
    """MVP only supports Level 1-2."""
    token = await _register_and_login(async_client, "pub8@example.com")
    bad_task = {**_VALID_TASK, "task_level": 3}
    resp = await async_client.post(
        "/api/v1/tasks",
        json=bad_task,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_task_returns_404(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
