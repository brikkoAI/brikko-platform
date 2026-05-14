"""Sprint 8 F5 — bulk operations on API keys.

Two endpoints:

* ``POST /v1/keys/bulk-revoke`` — revoke up to 50 keys in one call.
* ``POST /v1/keys/bulk-rotate``  — generate replacements + 7-day grace.

Both share:

* hard cap of 50 IDs per call (Pydantic validates max_items)
* cross-account isolation (foreign IDs go to ``not_found``, no info leak)
* one audit_log row per batch
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    AuditLog,
    Tariff,
    User,
)

_PASSWORD = "correct horse battery staple"


async def _seed_user(db, *, tariff: Tariff = Tariff.PRO) -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Bulk-test acc",
            balance_kopecks=100_000,
            tariff=tariff,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _login(client, user) -> None:
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 200, r.text


async def _create_key(client, *, name: str = "k") -> dict:
    r = await client.post(
        "/v1/keys",
        json={"name": name, "scope": "write"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# bulk-revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_revoke_marks_all_active_as_revoked(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    k1 = await _create_key(client, name="k1")
    k2 = await _create_key(client, name="k2")
    k3 = await _create_key(client, name="k3")

    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": [k1["id"], k2["id"], k3["id"]]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["revoked"]) == {k1["id"], k2["id"], k3["id"]}
    assert body["skipped"] == []
    assert body["not_found"] == []

    # All three are flagged REVOKED in DB.
    rows = (await db.execute(select(ApiKey))).scalars().all()
    assert len(rows) == 3
    for r_ in rows:
        assert r_.status == ApiKeyStatus.REVOKED
        assert r_.revoked_at is not None


@pytest.mark.asyncio
async def test_bulk_revoke_skips_already_revoked(client, db):
    user = await _seed_user(db)
    await _login(client, user)
    k1 = await _create_key(client, name="k1")
    k2 = await _create_key(client, name="k2")

    # Revoke k1 first via the per-item endpoint.
    r = await client.delete(f"/v1/keys/{k1['id']}", headers=await _csrf_headers(client))
    assert r.status_code == 204

    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": [k1["id"], k2["id"]]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["revoked"] == [k2["id"]]
    assert body["skipped"] == [k1["id"]]
    assert body["not_found"] == []


@pytest.mark.asyncio
async def test_bulk_revoke_cross_account_keys_go_to_not_found(client, db):
    """Keys belonging to another account land in ``not_found`` — no leak."""
    user = await _seed_user(db)
    await _login(client, user)
    own = await _create_key(client, name="own")

    # Forge another account + key directly in DB.
    other_user = User(
        email=f"o-{uuid.uuid4().hex[:6]}@x.test",
        password_hash="dummy",
        email_verified=True,
    )
    db.add(other_user)
    await db.flush()
    other_acc = Account(
        owner_id=other_user.id,
        name="Other",
        balance_kopecks=0,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        settings={},
    )
    db.add(other_acc)
    await db.flush()
    other_key = ApiKey(
        account_id=other_acc.id,
        name="other",
        key_hash="x",
        key_prefix="sk-brk-other00",
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(other_key)
    await db.commit()

    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": [own["id"], str(other_key.id)]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["revoked"] == [own["id"]]
    assert body["not_found"] == [str(other_key.id)]

    # Cross-account key MUST stay ACTIVE — we never touched it.
    await db.refresh(other_key)
    assert other_key.status == ApiKeyStatus.ACTIVE
    assert other_key.revoked_at is None


@pytest.mark.asyncio
async def test_bulk_revoke_over_50_rejected(client, db):
    user = await _seed_user(db)
    await _login(client, user)

    # We don't actually need real keys — Pydantic validates the size cap.
    too_many = [str(uuid.uuid4()) for _ in range(51)]
    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": too_many},
        headers=await _csrf_headers(client),
    )
    # Project's validation_exception_handler maps Pydantic 422 → 400.
    assert r.status_code == 400, r.text
    assert "50" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_bulk_revoke_empty_rejected(client, db):
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": []},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_revoke_writes_audit_row(client, db):
    user = await _seed_user(db)
    await _login(client, user)
    k1 = await _create_key(client, name="audited")

    r = await client.post(
        "/v1/keys/bulk-revoke",
        json={"key_ids": [k1["id"]]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200

    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "api_keys_bulk_revoked")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].meta is not None
    assert rows[0].meta["count"] == 1
    assert k1["id"] in rows[0].meta["key_ids"]


# ---------------------------------------------------------------------------
# bulk-rotate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_rotate_creates_new_keys_with_grace(client, db):
    user = await _seed_user(db)
    await _login(client, user)
    k1 = await _create_key(client, name="api-cli")
    k2 = await _create_key(client, name="api-web")

    r = await client.post(
        "/v1/keys/bulk-rotate",
        json={"key_ids": [k1["id"], k2["id"]]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grace_days"] == 7
    assert len(body["rotated"]) == 2
    assert body["skipped"] == []
    assert body["not_found"] == []

    for entry in body["rotated"]:
        assert entry["new_full_key"].startswith("sk-brk-")
        assert entry["old_id"] != entry["new_id"]
        # grace_until ≈ now + 7d
        until = datetime.fromisoformat(entry["grace_until"].replace("Z", "+00:00"))
        assert (until - datetime.now(UTC)).days >= 6

    # 2 old + 2 new = 4 keys in the DB.
    rows = (await db.execute(select(ApiKey))).scalars().all()
    assert len(rows) == 4
    # Old keys still ACTIVE but with expires_at set.
    old_ids = {uuid.UUID(k1["id"]), uuid.UUID(k2["id"])}
    olds = [k for k in rows if k.id in old_ids]
    assert len(olds) == 2
    for k in olds:
        assert k.status == ApiKeyStatus.ACTIVE
        assert k.expires_at is not None


@pytest.mark.asyncio
async def test_bulk_rotate_respects_tariff_cap(client, db):
    """Pro tariff cap = 10. Create 8 keys + try to rotate 5 → projected
    13 > cap → 403 ``key_limit_reached``."""
    user = await _seed_user(db, tariff=Tariff.PRO)
    await _login(client, user)
    keys = [await _create_key(client, name=f"k{i}") for i in range(8)]

    # Try rotating 5 → would push active count to 13 (8 active + 5 new).
    five_ids = [k["id"] for k in keys[:5]]
    r = await client.post(
        "/v1/keys/bulk-rotate",
        json={"key_ids": five_ids},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "key_limit_reached"

    # Nothing was rotated — DB state intact.
    rows = (await db.execute(select(ApiKey))).scalars().all()
    assert len(rows) == 8


@pytest.mark.asyncio
async def test_bulk_rotate_cross_account_keys_silent(client, db):
    user = await _seed_user(db)
    await _login(client, user)
    own = await _create_key(client, name="own")

    # Foreign key by UUID.
    foreign_uuid = uuid.uuid4()
    r = await client.post(
        "/v1/keys/bulk-rotate",
        json={"key_ids": [own["id"], str(foreign_uuid)]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["rotated"]) == 1
    assert body["rotated"][0]["old_id"] == own["id"]
    assert str(foreign_uuid) in body["not_found"]


@pytest.mark.asyncio
async def test_bulk_rotate_writes_audit_row(client, db):
    user = await _seed_user(db)
    await _login(client, user)
    k1 = await _create_key(client, name="rot-audit")

    r = await client.post(
        "/v1/keys/bulk-rotate",
        json={"key_ids": [k1["id"]]},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200
    new_id = r.json()["rotated"][0]["new_id"]

    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "api_keys_bulk_rotated")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].meta is not None
    assert rows[0].meta["count"] == 1
    assert k1["id"] in rows[0].meta["old_ids"]
    assert new_id in rows[0].meta["new_ids"]
    assert rows[0].meta["grace_days"] == 7
