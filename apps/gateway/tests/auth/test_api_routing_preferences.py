"""Integration tests for routing preferences (Sprint 7, spec v1.5/22).

Mapped to the Given-When-Then acceptance criteria from §7 of the spec.
The router-side behaviour (manual mode, custom whitelist, etc.) is
tested via the Router engine directly to avoid threading every chat
through the full provider mock.
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
    Tariff,
    User,
)
from voltari_gateway.router.router import (
    AccountContext,
    Router,
    RouterRequest,
    RoutingError,
)

_PASSWORD = "correct horse battery staple"


async def _seed_user(db) -> User:
    user = User(
        email=f"rp-{uuid.uuid4().hex[:10]}@example.com",
        password_hash=hash_password(_PASSWORD),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Acme",
            balance_kopecks=10_000,
            tariff=Tariff.PRO,
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


async def _account_for(db, user) -> Account:
    res = await db.execute(select(Account).where(Account.owner_id == user.id))
    return res.scalars().one()


# ---------------------------------------------------------------------------
# AC-RP-1: Default backwards-compat — fresh account uses cheap+smart_mode.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_routing_preferences_match_pre_sprint7(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    r = await client.get("/v1/account/routing-preferences")
    assert r.status_code == 200
    body = r.json()
    assert body["routing_mode"] == "smart"
    # CEO 30.04 §10 #6: stays "cheap" for new accounts to keep COGS predictable.
    assert body["routing_strategy"] == "cheap"
    assert body["allowed_providers"] is None
    assert body["allowed_models"] is None
    assert "preview" in body
    assert body["preview"]["active_models_total"] > 0


# ---------------------------------------------------------------------------
# AC-RP-2: Manual mode blocks auto:*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_mode_blocks_auto_tags():
    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="manual",
    )
    req = RouterRequest(model_tag="auto:smart", estimated_input_tokens=100)
    with pytest.raises(RoutingError) as exc:
        await router.route_request(req, account)
    assert exc.value.reason_code == "manual_mode_requires_pinned_model"


# ---------------------------------------------------------------------------
# AC-RP-3: Strategy ru_legal hard-filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ru_legal_strategy_only_picks_ru_models():
    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="smart",
        routing_strategy="ru_legal",
    )
    # ``auto:default`` translates to account.routing_strategy.
    req = RouterRequest(model_tag="auto:default", estimated_input_tokens=100)
    decision = await router.route_request(req, account)
    assert decision.primary.ru_legal
    for m in decision.fallback_chain:
        assert m.ru_legal


# ---------------------------------------------------------------------------
# AC-RP-5: Custom whitelist blocks pinned model outside the list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_whitelist_blocks_pinned_model_outside_list():
    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="smart",
        routing_strategy="custom",
        routing_allowed_providers=frozenset({"yandex"}),
    )
    req = RouterRequest(model_tag="gpt-5", estimated_input_tokens=100)
    with pytest.raises(RoutingError) as exc:
        await router.route_request(req, account)
    assert exc.value.reason_code == "model_not_in_allowed_providers"


# ---------------------------------------------------------------------------
# AC-RP-6: Custom whitelist + auto strategy → narrowed candidate set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_whitelist_narrows_auto_strategy_candidates():
    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="smart",
        routing_strategy="custom",
        routing_allowed_providers=frozenset({"openai", "anthropic"}),
    )
    req = RouterRequest(model_tag="auto:smart", estimated_input_tokens=100)
    decision = await router.route_request(req, account)
    assert decision.primary.provider.value in {"openai", "anthropic"}
    for m in decision.fallback_chain:
        assert m.provider.value in {"openai", "anthropic"}


# ---------------------------------------------------------------------------
# AC-RP-8: Validation — empty allowed_providers when strategy=custom
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_custom_with_empty_providers_400(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.put(
        "/v1/account/routing-preferences",
        json={
            "routing_mode": "smart",
            "routing_strategy": "custom",
            "allowed_providers": [],
        },
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "custom_requires_providers"

    # DB unchanged.
    account = await _account_for(db, user)
    await db.refresh(account)
    assert account.routing_strategy == "cheap"


# ---------------------------------------------------------------------------
# AC-RP-9: Audit log is written on update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_writes_audit_log_with_diff(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.put(
        "/v1/account/routing-preferences",
        json={"routing_mode": "smart", "routing_strategy": "ru_legal"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    audits = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "routing_preferences_updated")))
        .scalars()
        .all()
    )
    assert len(audits) >= 1
    diff = audits[0].meta.get("diff", {})
    assert "routing_strategy" in diff
    assert diff["routing_strategy"]["before"] == "cheap"
    assert diff["routing_strategy"]["after"] == "ru_legal"


# ---------------------------------------------------------------------------
# AC-RP-7: Single-provider whitelist disables failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_provider_whitelist_truncates_chain():
    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="smart",
        routing_strategy="custom",
        routing_allowed_providers=frozenset({"anthropic"}),
    )
    req = RouterRequest(model_tag="auto:smart", estimated_input_tokens=100)
    decision = await router.route_request(req, account)
    assert decision.primary.provider.value == "anthropic"
    # All fallbacks (if any) must also be anthropic — i.e. cross-provider
    # failover is disabled.
    for m in decision.fallback_chain:
        assert m.provider.value == "anthropic"


# ---------------------------------------------------------------------------
# AC-RP-10: Per-request exclude_providers intersects with allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_request_exclude_intersects_with_allowed():
    from voltari_gateway.router.catalog import Provider

    router = Router()
    account = AccountContext(
        account_id=uuid.uuid4(),
        tariff="pro",
        balance_kop=1_000,
        routing_mode="smart",
        routing_strategy="custom",
        routing_allowed_providers=frozenset({"openai", "anthropic", "yandex"}),
    )
    req = RouterRequest(
        model_tag="auto:smart",
        estimated_input_tokens=100,
        exclude_providers=frozenset({Provider.OPENAI}),
    )
    decision = await router.route_request(req, account)
    assert decision.primary.provider.value in {"anthropic", "yandex"}
    for m in decision.fallback_chain:
        assert m.provider.value in {"anthropic", "yandex"}


# ---------------------------------------------------------------------------
# AC-RP-12: Reset / PATCH back to defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_reset_clears_custom_state(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)

    headers = await _csrf_headers(client)
    await client.put(
        "/v1/account/routing-preferences",
        json={
            "routing_mode": "smart",
            "routing_strategy": "custom",
            "allowed_providers": ["anthropic"],
        },
        headers=headers,
    )

    headers2 = await _csrf_headers(client)
    r = await client.patch(
        "/v1/account/routing-preferences",
        json={"routing_strategy": "smart", "allowed_providers": None, "allowed_models": None},
        headers=headers2,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["routing_strategy"] == "smart"
    assert body["allowed_providers"] is None


# ---------------------------------------------------------------------------
# Validation — non-custom strategy with whitelist returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_custom_with_whitelist_rejected(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.put(
        "/v1/account/routing-preferences",
        json={
            "routing_mode": "smart",
            "routing_strategy": "smart",
            "allowed_providers": ["openai"],
        },
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "filter_requires_custom"


# ---------------------------------------------------------------------------
# Validation — unknown provider in allowed_providers returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_provider_rejected(client, db, redis_client):
    user = await _seed_user(db)
    await _login(client, user)
    headers = await _csrf_headers(client)
    r = await client.put(
        "/v1/account/routing-preferences",
        json={
            "routing_mode": "smart",
            "routing_strategy": "custom",
            "allowed_providers": ["openai", "deepmind"],
        },
        headers=headers,
    )
    # Pydantic validator emits 400 ``invalid_body`` (validation_exception_handler).
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Cross-user prevention — A's settings don't bleed into B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_preferences_are_per_account(client, db, redis_client):
    a = await _seed_user(db)
    b = await _seed_user(db)

    await _login(client, a)
    headers = await _csrf_headers(client)
    await client.put(
        "/v1/account/routing-preferences",
        json={"routing_mode": "smart", "routing_strategy": "ru_legal"},
        headers=headers,
    )

    # B's prefs are untouched.
    bob_acc = await _account_for(db, b)
    await db.refresh(bob_acc)
    assert bob_acc.routing_strategy == "cheap"
