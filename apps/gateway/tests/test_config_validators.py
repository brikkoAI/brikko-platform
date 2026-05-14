"""Validators on the Settings model.

Why we test this: a missing ``.env`` used to silently fall through to
the dev-default ``JWT_SECRET="dev-only-jwt-secret-change-me"`` — anyone
who guessed the constant could mint access tokens. The validator added
in BE P0-7 makes that impossible in production-like environments while
keeping dev/test friendly.

The tests rely on instantiating ``Settings`` directly (no ``.env`` is
loaded because ``model_config.env_file=".env"`` and we run from a
working dir without one) and overriding env vars via ``monkeypatch``.

Each test does ``cfg.get_settings.cache_clear()`` itself rather than
relying on the auth conftest fixture — these tests don't import the
auth subtree.
"""

from __future__ import annotations

import warnings

import pytest

from voltari_gateway import config as cfg
from voltari_gateway.config import AppEnv, Settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


# ---------- secret strength ------------------------------------------------


def test_dev_secret_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production must refuse the canonical dev-default JWT secret.

    Prior to this validator a missing ``JWT_SECRET`` env var resulted in a
    bootable production process where every token was signed with a
    publicly-guessable string.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "dev-only-jwt-secret-change-me")
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "strong-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        "strong-encryption-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "JWT_SECRET" in str(excinfo.value)
    assert "forbidden marker" in str(excinfo.value)


def test_short_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 16-byte secret must be rejected in staging/production."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("JWT_SECRET", "x" * 16)  # 16 < 32 byte floor
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "strong-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        "strong-encryption-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "JWT_SECRET" in str(excinfo.value)
    assert "shorter than" in str(excinfo.value)


def test_dev_secret_in_local_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local environment should warn but not crash on dev-default."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("JWT_SECRET", "dev-only-jwt-secret-change-me")
    monkeypatch.delenv("EMAIL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        s = Settings()

    # Settings instantiated successfully, app_env is local.
    assert s.app_env is AppEnv.LOCAL
    # And there's at least one warning mentioning JWT_SECRET.
    msgs = [str(w.message) for w in captured]
    assert any("JWT_SECRET" in m for m in msgs), msgs


def test_strong_secret_in_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real production-grade secret must be accepted."""
    # 48-byte random-looking string with no forbidden substrings.
    strong = "X9zT4kQ7mE2bN8wL5pV6jH3fR1cY0aD" + "ZqW1uI4oP9sB7nM" * 3
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", strong)
    monkeypatch.setenv("EMAIL_TOKEN_SECRET", strong)
    monkeypatch.setenv("ENCRYPTION_KEY", strong)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = Settings()

    assert s.app_env is AppEnv.PRODUCTION
    assert s.jwt_secret.get_secret_value() == strong


def test_empty_jwt_secret_in_production_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty JWT_SECRET in production must abort boot.

    JWT_SECRET is the only secret we never tolerate empty (we always sign
    access tokens). EMAIL_TOKEN_SECRET / ENCRYPTION_KEY allow empty in dev.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "strong-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        "strong-encryption-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    with pytest.raises(ValueError) as excinfo:
        Settings()
    assert "JWT_SECRET" in str(excinfo.value)


# ---------- app_env enum ---------------------------------------------------


def test_app_env_accepts_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """``APP_ENV=development`` used to crash with ``ValidationError``.

    Now it resolves to ``AppEnv.DEVELOPMENT`` and behaves as dev-like.
    """
    monkeypatch.setenv("APP_ENV", "development")
    s = Settings()
    assert s.app_env is AppEnv.DEVELOPMENT
    assert s.app_env.is_dev_like
    assert not s.app_env.is_production_like


def test_app_env_accepts_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI sometimes sets ``APP_ENV=test``."""
    monkeypatch.setenv("APP_ENV", "test")
    s = Settings()
    assert s.app_env is AppEnv.TEST


def test_app_env_alias_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common shorthand ``dev`` is normalised to development."""
    monkeypatch.setenv("APP_ENV", "dev")
    s = Settings()
    assert s.app_env is AppEnv.DEVELOPMENT


def test_app_env_alias_prod_requires_strong_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``prod`` alias maps to production, so it must enforce hygiene."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "dev-only-jwt-secret-change-me")
    with pytest.raises(ValueError):
        Settings()


def test_app_env_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bogus values still raise — we only added narrow aliases."""
    monkeypatch.setenv("APP_ENV", "qa-7")
    with pytest.raises(Exception):  # pydantic ValidationError wraps ValueError
        Settings()


# ---------- cookie_secure computed field -----------------------------------


def test_cookie_secure_default_in_local_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old default (cookie_secure=True + app_env=local) silently dropped
    cookies on http://localhost. The computed field flips it to False
    in dev-like envs unless explicitly overridden."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    s = Settings()
    assert s.cookie_secure is False


def test_cookie_secure_default_in_production_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "JWT_SECRET",
        "strong-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "strong-email-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        "strong-encryption-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    s = Settings()
    assert s.cookie_secure is True


def test_cookie_secure_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit env var overrides the computed default in either direction."""
    # Production with explicit COOKIE_SECURE=false (e.g. behind TLS terminator
    # that runs HTTP between the LB and the app).
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "JWT_SECRET",
        "strong-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "EMAIL_TOKEN_SECRET",
        "strong-email-pepper-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv(
        "ENCRYPTION_KEY",
        "strong-encryption-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    monkeypatch.setenv("COOKIE_SECURE", "false")
    s = Settings()
    assert s.cookie_secure is False
    assert s.cookie_secure_override is False
