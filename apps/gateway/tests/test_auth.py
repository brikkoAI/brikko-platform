"""Auth middleware tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from voltari_gateway.auth.keys import (
    KEY_PREFIX_LITERAL,
    extract_prefix,
    generate_api_key,
    verify_api_key,
)
from voltari_gateway.auth.middleware import _cache_key
from voltari_gateway.db.models import ApiKeyStatus


def test_generated_key_format():
    g = generate_api_key()
    assert g.plaintext.startswith(KEY_PREFIX_LITERAL)
    assert len(g.plaintext) > 14
    assert g.prefix == g.plaintext[:14]
    assert g.key_hash.startswith("$argon2id$")


def test_generated_key_verifies_against_hash():
    g = generate_api_key()
    assert verify_api_key(g.plaintext, g.key_hash) is True
    assert verify_api_key("not-the-right-thing", g.key_hash) is False
    assert verify_api_key(g.plaintext + "x", g.key_hash) is False


def test_extract_prefix_rejects_garbage():
    assert extract_prefix("") is None
    assert extract_prefix("garbage") is None
    assert extract_prefix("Bearer xyz") is None
    g = generate_api_key()
    assert extract_prefix(g.plaintext) == g.prefix


@pytest.mark.asyncio
async def test_models_endpoint_requires_valid_key(client, api_key_fixture):
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert any(m["id"] == "gpt-5.4-mini" for m in body["data"])


@pytest.mark.asyncio
async def test_models_endpoint_rejects_missing_token(client):
    r = await client.get("/v1/models")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_models_endpoint_rejects_invalid_token(client):
    r = await client.get("/v1/models", headers={"Authorization": "Bearer sk-vlt-bogusbogus"})
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_revoked_key_rejected(client, db, api_key_fixture, redis_client):
    # First call succeeds
    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200

    # Revoke and clear the auth cache so the next call hits the DB
    await redis_client.flushall()
    api_key_fixture.api_key.status = ApiKeyStatus.REVOKED
    api_key_fixture.api_key.revoked_at = datetime.now(UTC)
    db.add(api_key_fixture.api_key)
    await db.commit()

    r2 = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_redis_cache_hit_short_circuits_db(
    client, api_key_fixture, redis_client, monkeypatch
):
    # Prime the cache directly with a synthetic principal
    payload = {
        "account_id": str(api_key_fixture.account.id),
        "api_key_id": str(api_key_fixture.api_key.id),
        "user_id": str(api_key_fixture.user.id),
        "tariff": "pro",
        "balance_kopecks": 999_999,
        "store_prompts": True,
    }
    await redis_client.setex(_cache_key(api_key_fixture.plaintext), 60, json.dumps(payload))

    # Make DB verify blow up to prove we never reach it
    from voltari_gateway.auth import middleware as mw

    async def _fail(*_a, **_kw):
        raise AssertionError("should not be called when cache is hot")

    monkeypatch.setattr(mw, "_verify_against_db", _fail)

    r = await client.get("/v1/models", headers=api_key_fixture.auth_header)
    assert r.status_code == 200
