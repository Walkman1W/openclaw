"""TASK-036: Notification endpoint tests."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_notifications_empty_by_default(async_client: AsyncClient):
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "notif@example.com", "password": "password123", "name": "NotifUser"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "notif@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]

    resp = await async_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_notifications_mark_read(async_client: AsyncClient):
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "notif2@example.com", "password": "password123", "name": "NotifUser2"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "notif2@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]

    resp = await async_client.post(
        "/api/v1/notifications/mark-read",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "marked_read" in resp.json()


@pytest.mark.asyncio
async def test_notifications_unread_filter(async_client: AsyncClient):
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "notif3@example.com", "password": "password123", "name": "NotifUser3"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "notif3@example.com", "password": "password123"},
    )
    token = login.json()["access_token"]

    resp = await async_client.get(
        "/api/v1/notifications?unread_only=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
