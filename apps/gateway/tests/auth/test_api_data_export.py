"""Integration tests for personal data export (Sprint 7).

Covers:

* POST /v1/account/data-export → 202 + async worker writes a ZIP
* Rate limit: a second request within 24h → 429
* GET /v1/account/data-export/{id} → status + download URL
* GET /v1/account/data-export/{id}/download → streams the ZIP
* Cross-user prevention: 404 when accessing another user's export
* Expiry: 410 Gone after expires_at
* Privacy contract: ZIP must not contain password_hash / totp / refresh_jti
* Cleanup cron deletes expired ZIPs and flips status
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.api.data_export import _build_export_zip
from voltari_gateway.auth.password import hash_password
from voltari_gateway.billing.data_export_cron import sweep_expired_exports
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    DataExport,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)

_PASSWORD = "correct horse battery staple"


@pytest.fixture
def tmp_export_dir(tmp_path, monkeypatch):
    """Point the export dir at a tmp_path so tests don't pollute ./data/exports."""
    p = tmp_path / "exports"
    p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_EXPORT_DIR", str(p))
    # Settings is lru_cached at module level — cleared by the autouse
    # fixture in tests/auth/conftest.py.
    return p


async def _seed_user(db, *, balance_kop: int = 50_000) -> User:
    user = User(
        email=f"exp-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
        # Plant a TOTP secret to verify the export does NOT include it.
        totp_secret_encrypted=b"verysecretbytes",
        totp_recovery_codes_hashed=["bcrypt$1$abc"],
        telegram_chat_id=12345,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Acme",
            balance_kopecks=balance_kop,
            tariff=Tariff.PRO,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
            settings={},
        )
    )
    db.add(
        Transaction(
            account_id=(await db.execute(select(Account).where(Account.owner_id == user.id)))
            .scalars()
            .one()
            .id,
            type=TransactionKind.TOPUP,
            amount_kopecks=balance_kop,
            ref_id=f"topup-{uuid.uuid4().hex[:8]}",
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


async def _account_for(db, user) -> Account:
    res = await db.execute(select(Account).where(Account.owner_id == user.id))
    return res.scalars().one()


async def _wait_for_export(client, export_id: uuid.UUID, *, timeout: float = 5.0) -> dict:
    """Poll the status endpoint until ``status == ready`` (or timeout)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/v1/account/data-export/{export_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == "ready":
            return body
        if body["status"] == "failed":
            pytest.fail(f"export failed: {body}")
        await asyncio.sleep(0.05)
    pytest.fail(f"export {export_id} did not complete within {timeout}s")
    return {}


# ---------------------------------------------------------------------------
# 1) POST → 202 → ZIP completes; status flips to ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_export_returns_202_and_completes(client, db, redis_client, tmp_export_dir):
    user = await _seed_user(db)
    await _login(client, user)

    headers = await _csrf_headers(client)
    r = await client.post("/v1/account/data-export", headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    export_id = uuid.UUID(body["id"])

    ready = await _wait_for_export(client, export_id)
    assert ready["status"] == "ready"
    assert ready["download_url"] is not None
    assert ready["file_size_bytes"] is not None and ready["file_size_bytes"] > 0


# ---------------------------------------------------------------------------
# 2) Rate limit: second request within 24h → 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_rate_limit_blocks_second_request(client, db, redis_client, tmp_export_dir):
    user = await _seed_user(db)
    await _login(client, user)

    headers = await _csrf_headers(client)
    r1 = await client.post("/v1/account/data-export", headers=headers)
    assert r1.status_code == 202

    r2 = await client.post("/v1/account/data-export", headers=headers)
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "data_export_rate_limited"

    # Drain the in-flight worker so it finishes before the test engine
    # tears down — otherwise SQLAlchemy logs a noisy
    # "Cannot operate on a closed database" stack trace on shutdown.
    await _wait_for_export(client, uuid.UUID(r1.json()["id"]))


# ---------------------------------------------------------------------------
# 3) Cross-user prevention: B can't see A's export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_cross_user_prevention(client, db, redis_client, tmp_export_dir):
    a = await _seed_user(db)
    b = await _seed_user(db)

    await _login(client, a)
    headers = await _csrf_headers(client)
    r = await client.post("/v1/account/data-export", headers=headers)
    export_id = r.json()["id"]

    # Drain the worker before switching user — keeps the engine quiet
    # during teardown.
    await _wait_for_export(client, uuid.UUID(export_id))

    # Logout a, login b
    await client.post("/v1/auth/logout", headers=headers)
    await _login(client, b)

    r2 = await client.get(f"/v1/account/data-export/{export_id}")
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "export_not_found"


# ---------------------------------------------------------------------------
# 4) Privacy contract: ZIP must not leak passwords / TOTP / refresh_jti
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_zip_excludes_secrets(db, session_factory, tmp_export_dir, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)

    # Pre-create the DataExport row that the worker would normally insert.
    settings = get_settings()
    row = DataExport(
        user_id=user.id,
        account_id=account.id,
        status="pending",
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=settings.data_export_ttl_days),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await _build_export_zip(
        factory=session_factory,
        export_id=row.id,
        export_dir=str(tmp_export_dir),
    )

    await db.refresh(row)
    assert row.status == "ready"
    zip_path = Path(tmp_export_dir) / row.storage_path
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {
            "account.json",
            "user.json",
            "keys.json",
            "transactions.json",
            "usage_events.json",
            "audit_log.json",
            "sessions.json",
            "README.txt",
        }.issubset(names)

        # Privacy assertions.
        user_json = json.loads(zf.read("user.json"))
        assert "password_hash" not in user_json
        assert "totp_secret_encrypted" not in user_json
        assert "totp_recovery_codes_hashed" not in user_json
        # Email/telegram remain — those are the user's own data.
        assert user_json.get("email") == user.email

        sessions_json = json.loads(zf.read("sessions.json"))
        for s in sessions_json:
            assert "refresh_jti" not in s

        # Transactions present + non-empty.
        txs = json.loads(zf.read("transactions.json"))
        assert len(txs) >= 1
        assert all("amount_kopecks" in t for t in txs)


# ---------------------------------------------------------------------------
# 5) Empty archive for a fresh user works (no secrets crash the worker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_empty_account_zip_succeeds(db, session_factory, tmp_export_dir, redis_client):
    user = User(
        email=f"empty-{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Solo",
        balance_kopecks=0,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
    )
    db.add(account)
    await db.flush()

    settings = get_settings()
    row = DataExport(
        user_id=user.id,
        account_id=account.id,
        status="pending",
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=settings.data_export_ttl_days),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await _build_export_zip(
        factory=session_factory,
        export_id=row.id,
        export_dir=str(tmp_export_dir),
    )

    await db.refresh(row)
    assert row.status == "ready"
    zip_path = Path(tmp_export_dir) / row.storage_path
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        # All JSON files exist; transactions/usage are empty arrays.
        assert json.loads(zf.read("transactions.json")) == []
        assert json.loads(zf.read("usage_events.json")) == []


# ---------------------------------------------------------------------------
# 6) Download endpoint streams the ZIP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_streams_zip_content(client, db, redis_client, tmp_export_dir):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.post("/v1/account/data-export", headers=headers)
    export_id = uuid.UUID(r.json()["id"])
    await _wait_for_export(client, export_id)

    r2 = await client.get(f"/v1/account/data-export/{export_id}/download")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "application/zip"
    assert "attachment" in r2.headers["content-disposition"]

    # Content is a valid ZIP with the manifest README.
    buf = io.BytesIO(r2.content)
    with zipfile.ZipFile(buf) as zf:
        assert "README.txt" in zf.namelist()


# ---------------------------------------------------------------------------
# 7) Expiry: 410 once expires_at passed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_410_after_expiry(client, db, redis_client, tmp_export_dir):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.post("/v1/account/data-export", headers=headers)
    export_id = uuid.UUID(r.json()["id"])
    await _wait_for_export(client, export_id)

    # Force-expire the row.
    await db.execute(select(DataExport).where(DataExport.id == export_id))
    row = (await db.execute(select(DataExport).where(DataExport.id == export_id))).scalars().one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    r2 = await client.get(f"/v1/account/data-export/{export_id}/download")
    assert r2.status_code == 410
    assert r2.json()["error"]["code"] == "export_expired"


# ---------------------------------------------------------------------------
# 8) Cleanup cron unlinks ZIPs and flips status to expired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_cron_expires_old_exports(db, session_factory, tmp_export_dir, redis_client):
    user = await _seed_user(db)
    account = await _account_for(db, user)
    row = DataExport(
        user_id=user.id,
        account_id=account.id,
        status="ready",
        requested_at=datetime.now(UTC) - timedelta(days=10),
        ready_at=datetime.now(UTC) - timedelta(days=10),
        expires_at=datetime.now(UTC) - timedelta(days=1),
        storage_path=f"{uuid.uuid4()}.zip",
        size_bytes=42,
    )
    db.add(row)
    await db.commit()

    # Drop a real file so we can confirm unlink.
    fake_zip = Path(tmp_export_dir) / row.storage_path
    fake_zip.write_bytes(b"PK\x03\x04stub")
    assert fake_zip.exists()

    swept = await sweep_expired_exports(db, export_dir=str(tmp_export_dir))
    await db.commit()
    assert swept == 1
    assert not fake_zip.exists()

    await db.refresh(row)
    assert row.status == "expired"
    assert row.download_url is None
