"""Tests for the health endpoints.

* ``/healthz``        — process-liveness, always 200.
* ``/health/ready``   — DB + Redis + provider registry checks. Returns 503
                         when DB or Redis is down so reverse proxies / k8s
                         can pull the instance out of rotation.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_ok(client) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_health_ready_all_green(client) -> None:
    r = await client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"
    # Stub provider registers OPENAI; conftest does that explicitly.
    assert body["checks"]["providers"].get("openai") == "ok"


@pytest.mark.asyncio
async def test_health_ready_503_when_db_down(client, monkeypatch) -> None:
    """If the DB ping raises, ``/health/ready`` must return 503 + status=down.

    We patch the ``_check_db`` helper instead of poisoning the real engine —
    avoids leaking a broken engine into other tests sharing the module.
    """
    from voltari_gateway.api import health as health_mod

    async def boom(*_: object, **__: object) -> str:
        return "down"

    monkeypatch.setattr(health_mod, "_check_db", boom)

    r = await client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "down"
    assert body["checks"]["database"] == "down"
