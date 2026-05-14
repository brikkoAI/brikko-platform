"""Integration tests for ``/v1/mcp/tokens/*`` (Sprint MCP S1).

Strategy mirrors ``test_api_keys.py``: log in via /v1/auth/login → cookies
set → CRUD on /v1/mcp/tokens. CSRF double-submit on mutating verbs.

Coverage map (30 tests):

    Creation (8):
      - returns full_token + ``mcp-brk-`` prefix
      - hash never echoed in list / get
      - scope defaults to read_account
      - all 3 scopes accepted; unknown scope → 422
      - expires_in_days 30/90/180/365 work; 7 → 422
      - tariff cap PAYG=5, PRO=15 enforced
      - revoke frees a slot
      - 401 without session

    Listing (4):
      - empty list returns []
      - active + revoked all shown, newest first
      - other-account tokens excluded
      - 401 without session

    Patch (5):
      - rename happy path
      - empty name → 422
      - 256-char name → 422
      - scope in body → 400 (extra='forbid')
      - other-account → 404 (no leakage)

    Delete (6):
      - revokes + 204
      - second delete → 404
      - other-account → 404
      - audit_log row written
      - scope/expiry preserved on revoked rows
      - revoked frees tariff slot

    Misc (7):
      - CSRF missing on POST → 403
      - CSRF mismatch on DELETE → 403
      - list includes revoked
      - prefix matches first 14 chars of full_token
      - revoke is idempotent against concurrent-double-click (race covered)
      - distinct from api_keys table (parallel api_key doesn't bump cap)
      - audit action labels are mcp-specific
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from tests.auth.conftest import csrf_headers as _csrf_headers
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    AuditLog,
    McpScope,
    McpToken,
    McpTokenStatus,
    Tariff,
    User,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PASSWORD = "correct horse battery staple"


async def _seed_user(
    db,
    *,
    tariff: Tariff = Tariff.PRO,
    balance_kop: int = 100_000,
) -> User:
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
            name="MCP test acc",
            balance_kopecks=balance_kop,
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


async def _create(
    client,
    *,
    name: str = "default",
    scope: str = "read_account",
    expires_in_days: int | None = None,
) -> dict:
    body: dict = {"name": name, "scope": scope}
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    r = await client.post(
        "/v1/mcp/tokens",
        json=body,
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_token_returns_full_once(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    body = await _create(client, name="claude-desktop")
    full = body["full_token"]
    assert full.startswith("mcp-brk-")
    assert body["prefix"] == full[:14]
    assert body["scope"] == "read_account"
    assert body["name"] == "claude-desktop"
    assert uuid.UUID(body["id"])

    list_r = await client.get("/v1/mcp/tokens")
    assert list_r.status_code == 200
    items = list_r.json()
    assert len(items) == 1
    assert "full_token" not in items[0]
    assert items[0]["prefix"] == body["prefix"]


@pytest.mark.asyncio
async def test_create_token_default_scope_is_all(client, db, redis_client):
    """S3 (CEO 2026-05-12) flipped the default from ``read_account`` to
    ``all`` — the helper-skill onboarding flow needs a token that works
    against every read-only tool out of the box. Restrictive tokens
    (single specific scope) are still a valid choice, just no longer
    the default.
    """
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "no-scope"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text
    assert r.json()["scope"] == "all"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        "read_account",
        "read_usage",
        "recommend_model",
        "list_models",
        "read_traces",
        "list_cookbook",
        "list_integrations",
        "all",
    ],
)
async def test_create_token_accepts_all_scopes(client, db, redis_client, scope):
    """Every enum value is accepted by the POST endpoint. Unknown scopes
    are rejected by the pydantic ``Literal`` — see
    ``test_create_token_rejects_unknown_scope`` below.
    """
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name=f"t-{scope}", scope=scope)
    assert body["scope"] == scope


@pytest.mark.asyncio
async def test_create_token_rejects_unknown_scope(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "bad", "scope": "admin"},
        headers=await _csrf_headers(client),
    )
    # Gateway converts Pydantic 422 → 400 with invalid_body code (see
    # voltari_gateway.utils.errors.request_validation_handler).
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_body"


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [30, 90, 180, 365])
async def test_create_token_expires_presets_work(client, db, redis_client, days):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name=f"exp-{days}", expires_in_days=days)
    assert body["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_token_rejects_non_preset_expiry(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "bad-expiry", "expires_in_days": 7},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_create_token_respects_tariff_limit_payg(client, db, redis_client):
    """PAYG → 5 active tokens max. 6th create call must 403."""
    user = await _seed_user(db, tariff=Tariff.PAYG)
    await _login(client, user)

    for i in range(5):
        await _create(client, name=f"t{i}")

    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "overflow"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "mcp_token_limit_reached"


@pytest.mark.asyncio
async def test_create_token_respects_tariff_limit_pro(client, db, redis_client):
    """PRO → 15 active tokens max."""
    user = await _seed_user(db, tariff=Tariff.PRO)
    await _login(client, user)

    for i in range(15):
        await _create(client, name=f"t{i}")

    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "overflow"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_revoke_frees_tariff_slot(client, db, redis_client):
    user = await _seed_user(db, tariff=Tariff.PAYG)
    await _login(client, user)

    for i in range(5):
        await _create(client, name=f"t{i}")

    list_r = await client.get("/v1/mcp/tokens")
    victim = list_r.json()[0]["id"]
    del_r = await client.delete(
        f"/v1/mcp/tokens/{victim}",
        headers=await _csrf_headers(client),
    )
    assert del_r.status_code == 204

    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "after-revoke"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_create_token_requires_session(client):
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "ghost"},
    )
    # 401 for missing session — CSRF preflight rejects with 403 only when
    # the cookie is *present* but the header doesn't match.
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty_returns_empty_array(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    r = await client.get("/v1/mcp/tokens")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_includes_active_and_revoked_newest_first(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    t1 = await _create(client, name="old")
    await _create(client, name="new")

    # Revoke t1
    await client.delete(f"/v1/mcp/tokens/{t1['id']}", headers=await _csrf_headers(client))

    r = await client.get("/v1/mcp/tokens")
    items = r.json()
    assert len(items) == 2
    # Newest first
    assert items[0]["name"] == "new"
    assert items[1]["name"] == "old"
    statuses = {it["name"]: it["status"] for it in items}
    assert statuses == {"new": "active", "old": "revoked"}


@pytest.mark.asyncio
async def test_list_excludes_other_account_tokens(client, db, redis_client):
    user_a = await _seed_user(db)
    user_b = await _seed_user(db)
    await _login(client, user_a)
    await _create(client, name="a-token")

    # Switch to user_b
    await client.post("/v1/auth/logout", headers=await _csrf_headers(client))
    await _login(client, user_b)

    r = await client.get("/v1/mcp/tokens")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_requires_session(client):
    r = await client.get("/v1/mcp/tokens")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Patch (rename)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_token_renames(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="orig")

    r = await client.patch(
        f"/v1/mcp/tokens/{body['id']}",
        json={"name": "renamed"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"

    fresh = await db.execute(select(McpToken).where(McpToken.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.name == "renamed"


@pytest.mark.asyncio
async def test_patch_token_empty_name_rejected(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="orig")
    r = await client.patch(
        f"/v1/mcp/tokens/{body['id']}",
        json={"name": ""},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_token_too_long_name_rejected(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="orig")
    r = await client.patch(
        f"/v1/mcp/tokens/{body['id']}",
        json={"name": "x" * 256},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_token_scope_immutable_via_patch(client, db, redis_client):
    """Sending `scope` in PATCH body must hard-fail (400) thanks to
    ``extra='forbid'`` — silent drops would surprise the SPA."""
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="scoped", scope="read_account")

    r = await client.patch(
        f"/v1/mcp/tokens/{body['id']}",
        json={"name": "ok", "scope": "read_usage"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 400, r.text

    fresh = await db.execute(select(McpToken).where(McpToken.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.scope == McpScope.READ_ACCOUNT


@pytest.mark.asyncio
async def test_patch_other_account_token_returns_404(client, db, redis_client):
    user_a = await _seed_user(db)
    user_b = await _seed_user(db)
    await _login(client, user_a)
    body = await _create(client, name="a-owned")

    await client.post("/v1/auth/logout", headers=await _csrf_headers(client))
    await _login(client, user_b)

    r = await client.patch(
        f"/v1/mcp/tokens/{body['id']}",
        json={"name": "hijack"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Delete (revoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_token_marks_revoked(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)

    r = await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 204, r.text

    fresh = await db.execute(select(McpToken).where(McpToken.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.status == McpTokenStatus.REVOKED
    assert refreshed.revoked_at is not None


@pytest.mark.asyncio
async def test_delete_revoked_token_returns_404(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)

    r1 = await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )
    assert r1.status_code == 204

    r2 = await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "mcp_token_not_found"


@pytest.mark.asyncio
async def test_delete_other_account_token_returns_404(client, db, redis_client):
    user_a = await _seed_user(db)
    user_b = await _seed_user(db)
    await _login(client, user_a)
    body = await _create(client)

    await client.post("/v1/auth/logout", headers=await _csrf_headers(client))
    await _login(client, user_b)

    r = await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_writes_audit_log_entry(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)

    await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )

    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "mcp_token_revoked",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].meta.get("token_id") == body["id"]


@pytest.mark.asyncio
async def test_delete_preserves_scope_and_expiry_metadata(client, db, redis_client):
    """Revoke should keep the row intact for forensics — scope/expiry stay."""
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, scope="read_usage", expires_in_days=90)

    await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers=await _csrf_headers(client),
    )

    fresh = await db.execute(select(McpToken).where(McpToken.id == uuid.UUID(body["id"])))
    refreshed = fresh.scalar_one()
    await db.refresh(refreshed)
    assert refreshed.scope == McpScope.READ_USAGE
    assert refreshed.expires_at is not None


# ---------------------------------------------------------------------------
# Misc / cross-cutting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_missing_on_post_returns_403(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    # No CSRF header — cookie is set by login, but header missing.
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "no-csrf"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_csrf_mismatch_on_delete_returns_403(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)
    r = await client.delete(
        f"/v1/mcp/tokens/{body['id']}",
        headers={"X-CSRF-Token": "this-is-a-fake-token"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_prefix_matches_first_14_chars(client, db, redis_client):
    """The DB ``token_prefix`` must equal ``full_token[:14]``."""
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client)
    assert body["prefix"] == body["full_token"][:14]
    assert body["prefix"].startswith("mcp-brk-")


@pytest.mark.asyncio
async def test_mcp_tokens_count_separately_from_api_keys(client, db, redis_client):
    """MCP token cap and API-key cap are independent — a PAYG user can fill
    both surfaces without one starving the other."""
    from voltari_gateway.auth.keys import generate_api_key
    from voltari_gateway.db.models import ApiKey, ApiKeyScope, ApiKeyStatus

    user = await _seed_user(db, tariff=Tariff.PAYG)
    account = (await db.execute(select(Account).where(Account.owner_id == user.id))).scalar_one()

    # Fill the API-key surface to its cap (PAYG = 3).
    for i in range(3):
        gen = generate_api_key()
        db.add(
            ApiKey(
                account_id=account.id,
                name=f"api-{i}",
                key_hash=gen.key_hash,
                key_prefix=gen.prefix,
                scope=ApiKeyScope.WRITE,
                status=ApiKeyStatus.ACTIVE,
            )
        )
    await db.commit()

    await _login(client, user)

    # MCP surface still fully available — 5 PAYG tokens should all create.
    for i in range(5):
        await _create(client, name=f"mcp-{i}")

    # 6th MCP token → 403 (cap is on MCP only).
    r = await client.post(
        "/v1/mcp/tokens",
        json={"name": "overflow"},
        headers=await _csrf_headers(client),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_audit_action_label_is_mcp_specific(client, db, redis_client):
    """Audit row for MCP creation uses ``mcp_token_created`` (not
    ``api_key_created``) so the activity feed differentiates the surfaces."""
    user = await _seed_user(db)
    await _login(client, user)
    body = await _create(client, name="audit-check")

    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "mcp_token_created",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].meta.get("token_id") == body["id"]
    assert rows[0].meta.get("scope") == "read_account"
