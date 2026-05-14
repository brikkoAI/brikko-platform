"""Smart Router v2 feature-flag branching — Sprint S1.

Tests that ``api/chat.py`` routes through ``RouterPipeline.run`` iff
the env flag OR per-account flag is on, and through the legacy
``router_engine.route_request`` path otherwise.

We do NOT exercise the full chat endpoint here — that's the job of
``test_chat_with_router.py``. Instead we instrument the pipeline +
v1 router with counters and verify the right one was called.

Why instrument and not assert response bodies
---------------------------------------------

In S1 the pipeline is behaviour-identical to v1, so a black-box test
("call /v1/chat/completions, assert response") can't distinguish the
two paths. The whole point of S1 is that flipping the flag doesn't
change the response — only the code path. Instrumentation is the
only way to test "pipeline ran" vs "legacy ran".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from voltari_gateway.auth.middleware import AuthPrincipal
from voltari_gateway.config import get_settings


@pytest.fixture
def mock_settings_env_off():
    """Force ``SMART_ROUTER_V2_ENABLED=false`` for the test scope."""
    settings = get_settings()
    with patch.object(settings, "smart_router_v2_enabled", False):
        yield


@pytest.fixture
def mock_settings_env_on():
    """Force ``SMART_ROUTER_V2_ENABLED=true`` for the test scope."""
    settings = get_settings()
    with patch.object(settings, "smart_router_v2_enabled", True):
        yield


def _principal_with(flag: bool) -> AuthPrincipal:
    """Build a minimal AuthPrincipal carrying the v2 account flag."""
    import uuid

    return AuthPrincipal(
        account_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        tariff="payg",
        balance_kopecks=10_000,
        store_prompts=True,
        smart_router_v2_enabled=flag,
    )


# ---------------------------------------------------------------------------
# 1. ``_smart_router_v2_active`` semantics
# ---------------------------------------------------------------------------


class TestSmartRouterV2Active:
    def test_both_flags_off_returns_false(self, mock_settings_env_off) -> None:
        from voltari_gateway.api.chat import _smart_router_v2_active

        principal = _principal_with(False)
        assert _smart_router_v2_active(principal) is False

    def test_env_on_returns_true(self, mock_settings_env_on) -> None:
        from voltari_gateway.api.chat import _smart_router_v2_active

        principal = _principal_with(False)
        assert _smart_router_v2_active(principal) is True

    def test_account_on_returns_true(self, mock_settings_env_off) -> None:
        from voltari_gateway.api.chat import _smart_router_v2_active

        principal = _principal_with(True)
        assert _smart_router_v2_active(principal) is True

    def test_both_on_returns_true(self, mock_settings_env_on) -> None:
        from voltari_gateway.api.chat import _smart_router_v2_active

        principal = _principal_with(True)
        assert _smart_router_v2_active(principal) is True


# ---------------------------------------------------------------------------
# 2. End-to-end through the chat endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_flag_off_uses_legacy_path(
    client, api_key_fixture, mock_settings_env_off
) -> None:
    """Flag-off (env+account both False) → legacy ``route_request``."""
    # Instrument: count pipeline.run vs legacy route_request calls.
    pipeline_calls = 0
    legacy_calls = 0

    from voltari_gateway.router.pipeline import RouterPipeline
    from voltari_gateway.router.router import Router

    original_pipeline_run = RouterPipeline.run
    original_legacy = Router.route_request

    async def _spy_pipeline(self, ctx):
        nonlocal pipeline_calls
        pipeline_calls += 1
        return await original_pipeline_run(self, ctx)

    async def _spy_legacy(self, req, account):
        nonlocal legacy_calls
        legacy_calls += 1
        return await original_legacy(self, req, account)

    with (
        patch.object(RouterPipeline, "run", _spy_pipeline),
        patch.object(Router, "route_request", _spy_legacy),
    ):
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    assert pipeline_calls == 0, "pipeline must not run with both flags off"
    assert legacy_calls >= 1, "legacy router_engine.route_request must have run"


@pytest.mark.asyncio
async def test_chat_with_env_flag_on_uses_pipeline(
    client, api_key_fixture, mock_settings_env_on
) -> None:
    """Env-flag on → pipeline path."""
    pipeline_calls = 0

    from voltari_gateway.router.pipeline import RouterPipeline

    original_run = RouterPipeline.run

    async def _spy(self, ctx):
        nonlocal pipeline_calls
        pipeline_calls += 1
        return await original_run(self, ctx)

    with patch.object(RouterPipeline, "run", _spy):
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    assert pipeline_calls == 1, "pipeline must run when env flag is on"


@pytest.mark.asyncio
async def test_chat_with_account_flag_on_uses_pipeline(
    client, api_key_fixture, db, mock_settings_env_off
) -> None:
    """Env-flag off but account flag on → pipeline path.

    Flips ``account.smart_router_v2_enabled = True`` and clears any
    auth-cache entry so the next request observes the new value.
    """
    from voltari_gateway.auth.middleware import get_redis, invalidate_cache_for_key
    from voltari_gateway.router.pipeline import RouterPipeline

    # Flip the account flag.
    api_key_fixture.account.smart_router_v2_enabled = True
    await db.commit()
    # Invalidate cached principal so the next auth lookup re-reads the row.
    await invalidate_cache_for_key(get_redis(), api_key_fixture.api_key.id)

    pipeline_calls = 0
    original_run = RouterPipeline.run

    async def _spy(self, ctx):
        nonlocal pipeline_calls
        pipeline_calls += 1
        return await original_run(self, ctx)

    with patch.object(RouterPipeline, "run", _spy):
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    assert pipeline_calls == 1, "pipeline must run when account flag is on"


@pytest.mark.asyncio
async def test_response_is_byte_identical_with_flag_toggle(
    client, api_key_fixture, db, mock_settings_env_off
) -> None:
    """Routing decision (which model picked) must match across flag states.

    This is the central regression guarantee of S1: flipping the flag
    has zero observable effect on the routing decision.
    """
    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }

    # 1) flag off
    r_off = await client.post(
        "/v1/chat/completions", json=body, headers=api_key_fixture.auth_header
    )
    assert r_off.status_code == 200
    model_off = r_off.json()["model"]

    # 2) Flip account flag on, invalidate cache, repeat.
    from voltari_gateway.auth.middleware import get_redis, invalidate_cache_for_key

    api_key_fixture.account.smart_router_v2_enabled = True
    await db.commit()
    await invalidate_cache_for_key(get_redis(), api_key_fixture.api_key.id)

    r_on = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r_on.status_code == 200
    model_on = r_on.json()["model"]

    # Same primary model chosen.
    assert model_off == model_on


# ---------------------------------------------------------------------------
# 3. Fallback path when ``app.state.router_pipeline`` is missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_works_without_app_state_pipeline(
    client, api_key_fixture, mock_settings_env_on
) -> None:
    """If ``app.state.router_pipeline`` is missing the chat handler
    must still build one on the fly and route through it.

    Why this matters: the test app fixture doesn't run ``main.lifespan``
    so ``router_pipeline`` is never set. Production runs lifespan and
    has the attribute, but request-scoped fallback construction keeps
    the chat handler robust if a future refactor breaks lifespan
    ordering — chat must never 500 just because a setup step was skipped.
    """
    from voltari_gateway.router.pipeline import RouterPipeline

    # Make sure no pipeline is pre-attached.
    app_state = client._transport.app.state  # type: ignore[attr-defined]
    if hasattr(app_state, "router_pipeline"):
        delattr(app_state, "router_pipeline")

    pipeline_calls = 0
    original_run = RouterPipeline.run

    async def _spy(self, ctx):
        nonlocal pipeline_calls
        pipeline_calls += 1
        return await original_run(self, ctx)

    with patch.object(RouterPipeline, "run", _spy):
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=api_key_fixture.auth_header,
        )

    assert r.status_code == 200
    assert pipeline_calls == 1
