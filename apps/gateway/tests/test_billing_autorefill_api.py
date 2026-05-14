"""Sprint 8 F1 — autorefill REST endpoints + audit_log.

Schema, cron loop, ЮKassa integration: all already in place by Sprint 7.
This file covers the Sprint 8 additions:

* ``GET /v1/billing/autorefill`` returns the four autorefill columns.
* POST/DELETE write audit_log rows so the activity feed surfaces them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import AuditLog


@pytest.mark.asyncio
async def test_get_autorefill_default_disabled(client, api_key_fixture):
    """Brand-new account → enabled=False, all fields NULL."""
    r = await client.get(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["threshold_kopecks"] is None
    assert body["topup_kopecks"] is None
    assert body["payment_method_id"] is None


@pytest.mark.asyncio
async def test_post_autorefill_enables_and_writes_audit(client, api_key_fixture, db):
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm-saved-card-1",
            "threshold_kopecks": 100_00,
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["threshold_kopecks"] == 100_00
    assert body["topup_kopecks"] == 500_00

    # GET reflects the new state.
    r = await client.get(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
    )
    state = r.json()
    assert state["enabled"] is True
    assert state["payment_method_id"] == "pm-saved-card-1"

    # audit_log row written.
    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "autorefill_enabled")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].meta is not None
    assert rows[0].meta["threshold_kopecks"] == 100_00
    assert rows[0].meta["topup_kopecks"] == 500_00


@pytest.mark.asyncio
async def test_post_autorefill_threshold_validation(client, api_key_fixture):
    """topup_kopecks must be > threshold_kopecks."""
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm-saved-1",  # ≥8 chars per AutorefillRequest
            "threshold_kopecks": 500_00,
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "autorefill_threshold_invalid"


@pytest.mark.asyncio
async def test_post_then_post_emits_updated_action(client, api_key_fixture, db):
    """Re-configure → audit_log action is autorefill_updated, not _enabled."""
    # First enable.
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm-firstcard",
            "threshold_kopecks": 100_00,
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200

    # Re-configure.
    r = await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm-secondcard",
            "threshold_kopecks": 200_00,
            "topup_kopecks": 1_000_00,
        },
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200

    actions = [
        r.action
        for r in (await db.execute(select(AuditLog).order_by(AuditLog.created_at.asc())))
        .scalars()
        .all()
    ]
    assert "autorefill_enabled" in actions
    assert "autorefill_updated" in actions


@pytest.mark.asyncio
async def test_delete_autorefill_writes_audit(client, api_key_fixture, db):
    # Enable first.
    await client.post(
        "/v1/billing/autorefill",
        json={
            "payment_method_id": "pm-saved-disable",
            "threshold_kopecks": 100_00,
            "topup_kopecks": 500_00,
        },
        headers=api_key_fixture.auth_header,
    )

    r = await client.delete(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200
    assert r.json() == {"enabled": False}

    # GET reflects.
    r = await client.get(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
    )
    body = r.json()
    assert body["enabled"] is False
    assert body["payment_method_id"] is None

    # audit_log row.
    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "autorefill_disabled")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_no_op_when_already_disabled(client, api_key_fixture, db):
    """DELETE on already-disabled account → 200 but no audit row added."""
    r = await client.delete(
        "/v1/billing/autorefill",
        headers=api_key_fixture.auth_header,
    )
    assert r.status_code == 200

    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "autorefill_disabled")))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_get_autorefill_unauthenticated(client):
    r = await client.get("/v1/billing/autorefill")
    assert r.status_code == 401
