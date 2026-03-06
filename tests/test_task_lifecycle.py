"""Sprint 2 integration tests: task lifecycle from claim through settlement.

Tests run against the full FastAPI app with test DB and Redis DB 15.
Celery tasks are NOT invoked here (settlement is triggered manually via
the service layer in dedicated settlement tests).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FUTURE = "2099-01-01T00:00:00Z"

_VALID_CRITERIA = [
    {"type": "field_completeness", "required_fields": ["output"]},
]

_VALID_TASK = {
    "title": "Test extraction task",
    "task_type": "data_extraction",
    "task_level": 1,
    "input_spec": {"data": "some input"},
    "output_spec": {"format": "json"},
    "acceptance_criteria": _VALID_CRITERIA,
    "reward_pool": 20,
    "deadline": _FUTURE,
    "required_tags": ["data-extraction"],
}


async def _register_human(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "name": "Human"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    return resp.json()["access_token"]


async def _register_agent(client: AsyncClient, name: str = "AgentBot") -> str:
    resp = await client.post(
        "/api/v1/agents/register",
        json={"name": name, "capability_tags": ["data-extraction"]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["api_key"]


async def _publish_task(client: AsyncClient, token: str, overrides: dict | None = None) -> str:
    body = {**_VALID_TASK, **(overrides or {})}
    resp = await client.post(
        "/api/v1/tasks",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# GET /tasks/recommended (TASK-011)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_returns_matching_tasks(async_client: AsyncClient):
    token = await _register_human(async_client, "rec_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "RecAgent")
    resp = await async_client.get(
        "/api/v1/tasks/recommended",
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    tasks = resp.json()
    assert any(t["id"] == task_id for t in tasks)
    # All returned tasks must have a match_score
    assert all("match_score" in t for t in tasks)


@pytest.mark.asyncio
async def test_recommended_excludes_rep_too_low(async_client: AsyncClient):
    """Tasks with min_reputation > 0 should be hidden from agents with rep=0."""
    token = await _register_human(async_client, "rec_pub2@example.com")
    await _publish_task(async_client, token, {"min_reputation": 500, "required_tags": []})

    api_key = await _register_agent(async_client, "LowRepAgent")
    resp = await async_client.get(
        "/api/v1/tasks/recommended",
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    for t in resp.json():
        assert t["min_reputation"] == 0 or t["min_reputation"] <= 0


# ---------------------------------------------------------------------------
# POST /tasks/{id}/claim  (TASK-013)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_task_success(async_client: AsyncClient):
    token = await _register_human(async_client, "claim_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "ClaimAgent")
    resp = await async_client.post(
        f"/api/v1/tasks/{task_id}/claim",
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "claimed"


@pytest.mark.asyncio
async def test_claim_deducts_deposit(async_client: AsyncClient):
    token = await _register_human(async_client, "claim_pub2@example.com")
    task_id = await _publish_task(async_client, token, {"reward_pool": 100})

    api_key = await _register_agent(async_client, "DepositAgent")
    # Get balance before claim
    before = (await async_client.get(
        "/api/v1/claw/balance",
        headers={"x-api-key": api_key},
    )).json()["balance"]

    await async_client.post(
        f"/api/v1/tasks/{task_id}/claim",
        headers={"x-api-key": api_key},
    )

    after = (await async_client.get(
        "/api/v1/claw/balance",
        headers={"x-api-key": api_key},
    )).json()["balance"]

    # Deposit = 10% of 100 = 10
    assert after == before - 10


@pytest.mark.asyncio
async def test_claim_already_claimed_returns_409(async_client: AsyncClient):
    token = await _register_human(async_client, "claim_pub3@example.com")
    task_id = await _publish_task(async_client, token)

    api_key1 = await _register_agent(async_client, "FirstAgent")
    api_key2 = await _register_agent(async_client, "SecondAgent")

    r1 = await async_client.post(
        f"/api/v1/tasks/{task_id}/claim",
        headers={"x-api-key": api_key1},
    )
    assert r1.status_code == 200

    r2 = await async_client.post(
        f"/api/v1/tasks/{task_id}/claim",
        headers={"x-api-key": api_key2},
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# POST /tasks/{id}/start  (TASK-015)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_task_success(async_client: AsyncClient):
    token = await _register_human(async_client, "start_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "StartAgent")
    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key})

    resp = await async_client.post(
        f"/api/v1/tasks/{task_id}/start",
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_wrong_agent_cannot_start(async_client: AsyncClient):
    token = await _register_human(async_client, "start_pub2@example.com")
    task_id = await _publish_task(async_client, token)

    api_key1 = await _register_agent(async_client, "Claimer2")
    api_key2 = await _register_agent(async_client, "Intruder2")

    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key1})
    resp = await async_client.post(
        f"/api/v1/tasks/{task_id}/start",
        headers={"x-api-key": api_key2},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /tasks/{id}/submit + Layer 0 audit  (TASK-015/018)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_layer0_pass(async_client: AsyncClient):
    token = await _register_human(async_client, "sub_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "SubAgent")
    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key})
    await async_client.post(f"/api/v1/tasks/{task_id}/start", headers={"x-api-key": api_key})

    resp = await async_client.post(
        f"/api/v1/tasks/{task_id}/submit",
        json={"result_data": {"output": "extracted data"}},
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is True
    assert data["task_status"] == "under_audit"


@pytest.mark.asyncio
async def test_submit_layer0_fail_missing_field(async_client: AsyncClient):
    token = await _register_human(async_client, "sub_pub2@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "FailAgent")
    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key})
    await async_client.post(f"/api/v1/tasks/{task_id}/start", headers={"x-api-key": api_key})

    # Submit result missing the required 'output' field
    resp = await async_client.post(
        f"/api/v1/tasks/{task_id}/submit",
        json={"result_data": {"wrong_field": "data"}},
        headers={"x-api-key": api_key},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    assert data["task_status"] == "failed"


# ---------------------------------------------------------------------------
# GET /tasks/{id}/audit-result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_result(async_client: AsyncClient):
    token = await _register_human(async_client, "ar_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "AuditCheckAgent")
    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key})
    await async_client.post(f"/api/v1/tasks/{task_id}/start", headers={"x-api-key": api_key})
    await async_client.post(
        f"/api/v1/tasks/{task_id}/submit",
        json={"result_data": {"output": "done"}},
        headers={"x-api-key": api_key},
    )

    resp = await async_client.get(f"/api/v1/tasks/{task_id}/audit-result")
    assert resp.status_code == 200
    data = resp.json()
    assert data["layer"] == 0
    assert data["result"] in ("pass", "fail")
    assert "checks" in data


# ---------------------------------------------------------------------------
# GET /tasks/{id}/history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_history_shows_transitions(async_client: AsyncClient):
    token = await _register_human(async_client, "hist_pub@example.com")
    task_id = await _publish_task(async_client, token)

    api_key = await _register_agent(async_client, "HistoryAgent")
    await async_client.post(f"/api/v1/tasks/{task_id}/claim", headers={"x-api-key": api_key})
    await async_client.post(f"/api/v1/tasks/{task_id}/start", headers={"x-api-key": api_key})

    resp = await async_client.get(f"/api/v1/tasks/{task_id}/history")
    assert resp.status_code == 200
    history = resp.json()
    statuses = [h["to_status"] for h in history]
    assert "pending" in statuses
    assert "claimed" in statuses
    assert "in_progress" in statuses
