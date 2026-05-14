"""Integration tests for the social OAuth login API.

We don't talk to the real Google / Yandex APIs in tests; instead we
monkeypatch :func:`voltari_gateway.auth.oauth_providers.exchange_code_for_userinfo`
to return a synthetic :class:`OAuthUserInfo`. The state token, on the
other hand, IS real — we mint and verify HMAC tokens with the actual
itsdangerous serialiser so the test exercises signature + expiry paths.

Provider configuration: each test sets the env vars via monkeypatch so
``is_provider_configured()`` returns True without touching dotenv.

Twelve covered scenarios (one per ``test_*`` function) — see the table
in PR description.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway.auth.oauth_login_state import generate_state
from voltari_gateway.auth.oauth_providers import OAuthProviderError, OAuthUserInfo
from voltari_gateway.auth.password import hash_password
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    OAuthIdentity,
    Tariff,
    Transaction,
    User,
    WelcomeCreditsLog,
)

# ---------------------------------------------------------------------------
# Test fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _configure_providers(monkeypatch):
    """Make ``is_provider_configured()`` return True for both providers.

    Setting the env vars BEFORE the test body runs ensures any settings
    cache is harmless: the tests call ``get_settings()`` after the env
    is in place, and ``is_provider_configured`` re-reads on every call.
    """
    # Pydantic-settings caches behind ``lru_cache``; clear it so the
    # patched env actually takes effect.
    get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-google-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", "test-yandex-id")
    monkeypatch.setenv("YANDEX_OAUTH_CLIENT_SECRET", "test-yandex-secret")
    monkeypatch.setenv("OAUTH_LOGIN_REDIRECT_BASE", "https://api.test.local")
    monkeypatch.setenv("BASE_URL_FRONTEND", "https://app.test.local")
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _patch_userinfo(monkeypatch, info: OAuthUserInfo) -> list[str]:
    """Stub ``exchange_code_for_userinfo`` and capture the codes it sees."""
    captured: list[str] = []

    async def fake_exchange(provider: str, *, code: str) -> OAuthUserInfo:
        captured.append(code)
        return info

    monkeypatch.setattr(
        "voltari_gateway.api.oauth_login.exchange_code_for_userinfo",
        fake_exchange,
    )
    return captured


def _patch_userinfo_error(monkeypatch, code: str = "token_rejected") -> None:
    """Stub the userinfo call to raise an :class:`OAuthProviderError`."""

    async def fake_exchange(provider: str, *, code: str) -> OAuthUserInfo:
        raise OAuthProviderError("token_rejected", "stubbed failure")

    monkeypatch.setattr(
        "voltari_gateway.api.oauth_login.exchange_code_for_userinfo",
        fake_exchange,
    )


def _state_for(provider: str, *, mode: str = "login", next_path: str = "/app", user_id=None) -> str:
    return generate_state(
        provider=provider,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        next_path=next_path,
        user_id=user_id,
    )


async def _make_user_with_password(db, *, email: str | None = None) -> User:
    user = User(
        email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery"),
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        Account(
            owner_id=user.id,
            name="Existing acc",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# 1) /start happy path → 302 to provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_redirects_to_google_consent(client):
    r = await client.get("/v1/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=test-google-id" in location
    assert (
        "redirect_uri=https%3A%2F%2Fapi.test.local%2Fv1%2Fauth%2Foauth%2Fgoogle%2Fcallback"
        in location
    )
    assert "state=" in location


# ---------------------------------------------------------------------------
# 2) /start unknown provider → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_unknown_provider_returns_404(client):
    r = await client.get("/v1/auth/oauth/facebook/start", follow_redirects=False)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_provider"


# ---------------------------------------------------------------------------
# 3) /start unconfigured provider → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_unconfigured_provider_returns_503(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    r = await client.get("/v1/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "oauth_not_configured"


# ---------------------------------------------------------------------------
# 4) /callback signup happy path — Google verified email, welcome credit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_google_signup_grants_welcome_credit(client, db, redis_client, monkeypatch):
    info = OAuthUserInfo(
        provider="google",
        subject="g-12345",
        email="newuser@example.com",
        email_verified=True,
        display_name="New User",
        avatar_url="https://lh3.example/avatar.png",
    )
    _patch_userinfo(monkeypatch, info)

    state = _state_for("google", next_path="/app")
    r = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "fake-google-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "https://app.test.local/app"
    set_cookies = "\n".join(r.headers.get_list("set-cookie"))
    assert "vlt_access" in set_cookies
    assert "vlt_refresh" in set_cookies

    # Verify DB state
    user = (await db.execute(select(User).where(User.email == "newuser@example.com"))).scalar_one()
    assert user.email_verified is True

    identity = (
        await db.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == "google",
                OAuthIdentity.subject == "g-12345",
            )
        )
    ).scalar_one()
    assert identity.user_id == user.id
    assert identity.linked_via == "signup"
    assert identity.email_verified_at_link is True
    assert identity.last_login_at is not None

    account = (await db.execute(select(Account).where(Account.owner_id == user.id))).scalar_one()
    # 200 ₽ OAuth welcome + 100 ₽ anonymize signup bonus (BRIEF v2 §5) = 300 ₽.
    assert account.balance_kopecks == 30_000

    # Welcome credit log row + two TOPUP transaction rows (the legacy
    # 200 ₽ ``welcome`` + the anonymize 100 ₽ ``welcome_anonymize``).
    assert (await db.execute(select(WelcomeCreditsLog))).scalar_one() is not None
    txns = (
        (await db.execute(select(Transaction).where(Transaction.account_id == account.id)))
        .scalars()
        .all()
    )
    kinds = {t.meta["kind"] for t in txns if t.meta}
    assert kinds == {"welcome", "welcome_anonymize"}
    assert sum(t.amount_kopecks for t in txns) == 30_000


# ---------------------------------------------------------------------------
# 5) /callback Google verified email → auto-link to existing password user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_google_verified_email_auto_links(client, db, redis_client, monkeypatch):
    existing = await _make_user_with_password(db, email="alice@example.com")

    info = OAuthUserInfo(
        provider="google",
        subject="g-99999",
        email="alice@example.com",
        email_verified=True,
        display_name="Alice",
        avatar_url=None,
    )
    _patch_userinfo(monkeypatch, info)

    state = _state_for("google", next_path="/app")
    r = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://app.test.local/app"

    # No duplicate user; identity attached to the existing one.
    rows = (await db.execute(select(User).where(User.email == "alice@example.com"))).scalars().all()
    assert len(rows) == 1
    identity = (
        await db.execute(select(OAuthIdentity).where(OAuthIdentity.user_id == existing.id))
    ).scalar_one()
    assert identity.linked_via == "email_match"
    assert identity.email_verified_at_link is True

    # No welcome credit on auto-link (it would be a second grant for this user).
    txns = (
        (
            await db.execute(select(Transaction).where(Transaction.account_id != None))  # noqa: E711
        )
        .scalars()
        .all()
    )
    assert all(t.meta.get("kind") != "welcome" for t in txns)


# ---------------------------------------------------------------------------
# 6) /callback Yandex unverified email + email taken → no auto-link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_yandex_unverified_email_taken_blocks_auto_link(client, db, monkeypatch):
    await _make_user_with_password(db, email="bob@example.com")

    info = OAuthUserInfo(
        provider="yandex",
        subject="y-77",
        email="bob@example.com",
        email_verified=False,
        display_name="Bob",
        avatar_url=None,
    )
    _patch_userinfo(monkeypatch, info)

    state = _state_for("yandex", next_path="/app")
    r = await client.get(
        "/v1/auth/oauth/yandex/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://app.test.local/login?")
    assert "reason=oauth_email_taken" in loc

    # No identity created.
    assert (await db.execute(select(OAuthIdentity))).scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 7) /callback bad state → invalid_state redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_invalid_state(client):
    r = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "fake", "state": "definitely-not-a-real-state"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=oauth_invalid_state" in r.headers["location"]


# ---------------------------------------------------------------------------
# 8) /callback user cancelled at provider → cancelled redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_user_cancelled_at_provider(client):
    r = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=oauth_cancelled" in r.headers["location"]


# ---------------------------------------------------------------------------
# 9) /callback provider returns no email (Yandex sans email scope) → email_required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_no_email_redirects_to_email_required(client, monkeypatch):
    info = OAuthUserInfo(
        provider="yandex",
        subject="y-88",
        email=None,
        email_verified=False,
        display_name=None,
        avatar_url=None,
    )
    _patch_userinfo(monkeypatch, info)
    state = _state_for("yandex")

    r = await client.get(
        "/v1/auth/oauth/yandex/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "reason=oauth_email_required" in r.headers["location"]


# ---------------------------------------------------------------------------
# 10) Existing identity → login (no new user, no extra welcome credit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_existing_identity_logs_in(client, db, redis_client, monkeypatch):
    existing = await _make_user_with_password(db, email="charlie@example.com")
    db.add(
        OAuthIdentity(
            user_id=existing.id,
            provider="google",
            subject="g-loyal",
            email_at_link="charlie@example.com",
            email_verified_at_link=True,
            display_name="Charlie",
            avatar_url=None,
            linked_via="signup",
            last_login_at=None,
        )
    )
    await db.commit()

    info = OAuthUserInfo(
        provider="google",
        subject="g-loyal",
        email="charlie@example.com",
        email_verified=True,
        display_name="Charlie Updated",
        avatar_url="https://lh3.example/new.png",
    )
    _patch_userinfo(monkeypatch, info)
    state = _state_for("google", next_path="/app/dashboard")

    r = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://app.test.local/app/dashboard"
    set_cookies = "\n".join(r.headers.get_list("set-cookie"))
    assert "vlt_access" in set_cookies

    # Identity row updated, not duplicated.
    rows = (
        (await db.execute(select(OAuthIdentity).where(OAuthIdentity.subject == "g-loyal")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].display_name == "Charlie Updated"
    assert rows[0].last_login_at is not None

    # No welcome credit issued on plain login.
    txns = (await db.execute(select(Transaction))).scalars().all()
    assert all(t.meta.get("kind") != "welcome" for t in txns)


# ---------------------------------------------------------------------------
# 11) /disconnect blocks last login method when password is unusable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_refuses_when_last_method(client, db, redis_client, monkeypatch):
    # OAuth-only signup first.
    info = OAuthUserInfo(
        provider="google",
        subject="g-only",
        email="oauthonly@example.com",
        email_verified=True,
        display_name="OAuth Only",
        avatar_url=None,
    )
    _patch_userinfo(monkeypatch, info)
    state = _state_for("google")
    sign = await client.get(
        "/v1/auth/oauth/google/callback",
        params={"code": "fake", "state": state},
        follow_redirects=False,
    )
    assert sign.status_code == 302
    # Cookies are now on the AsyncClient.
    csrf = client.cookies.get("vlt_csrf")
    assert csrf is not None

    r = await client.post(
        "/v1/auth/oauth/disconnect",
        json={"provider": "google"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "last_login_method"


# ---------------------------------------------------------------------------
# 12) /disconnect succeeds when password fallback exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_succeeds_when_password_exists(client, db, redis_client, monkeypatch):
    # Existing password user with a Google identity attached.
    user = await _make_user_with_password(db, email="dave@example.com")
    db.add(
        OAuthIdentity(
            user_id=user.id,
            provider="google",
            subject="g-paired",
            email_at_link="dave@example.com",
            email_verified_at_link=True,
            display_name="Dave",
            avatar_url=None,
            linked_via="email_match",
            last_login_at=None,
        )
    )
    await db.commit()

    # Log in via password to get cookies.
    login = await client.post(
        "/v1/auth/login",
        json={"email": "dave@example.com", "password": "correct horse battery"},
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]

    r = await client.post(
        "/v1/auth/oauth/disconnect",
        json={"provider": "google"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    rows = (
        (await db.execute(select(OAuthIdentity).where(OAuthIdentity.user_id == user.id)))
        .scalars()
        .all()
    )
    assert rows == []
