"""Tests for the SMTP-not-configured fallback in ``POST /v1/auth/signup``.

When SMTP credentials are missing (the typical state during the brief
window between launch and the operator wiring SMTP_USER/SMTP_PASSWORD),
the signup endpoint must:

1. Log the verify URL with structured fields so an operator can recover
   it from journalctl (full token NEVER hits the log — only an 8-char
   prefix; the link itself contains the token but only ops sees the log).
2. Surface the verify URL in the JSON response under ``verify_url_dev``
   when the gateway is non-production OR when ``EXPOSE_DEV_VERIFY_URL``
   is explicitly set. In strict production with that flag off the field
   is omitted (we still want a controlled escape hatch, not a free
   verify-link leak).

When SMTP IS configured, behaviour is unchanged: ``send_email`` runs and
the response carries no ``verify_url_dev`` field.

These tests run on the auth-suite fixtures (``client``, ``db``,
``email_backend=console`` by default in ``tests/auth/conftest.py``). They
patch the runtime config switch ``is_smtp_configured`` directly — much
cleaner than juggling four env vars across processes — and patch
``send_email`` to assert it was/wasn't called.
"""

from __future__ import annotations

import uuid

import pytest

from voltari_gateway.email import client as email_client


def _patch_smtp_configured(monkeypatch, *, configured: bool) -> None:
    """Force ``is_smtp_configured`` to a deterministic boolean for the test.

    Patches BOTH the source module and the api/auth.py rebound name —
    same trick ``_capture_emails`` uses for ``send_email``. Without the
    second patch the import-time binding in ``api/auth.py`` keeps
    pointing at the original.
    """
    monkeypatch.setattr(email_client, "is_smtp_configured", lambda *a, **kw: configured)
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "is_smtp_configured", lambda *a, **kw: configured)


def _patch_send_email(monkeypatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []

    async def _fake_send(to: str, subject: str, body: str) -> None:
        captured.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    from voltari_gateway.api import auth as auth_api

    monkeypatch.setattr(auth_api, "send_email", _fake_send)
    return captured


# ---------------------------------------------------------------------------
# 1) SMTP NOT configured + non-production env → verify_url_dev present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_returns_verify_url_dev_when_smtp_unconfigured(client, db, monkeypatch):
    """Default test env is APP_ENV=local; the response should expose the link."""
    _patch_smtp_configured(monkeypatch, configured=False)
    captured = _patch_send_email(monkeypatch)

    email = f"dev-fallback-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Core contract preserved.
    assert body["email"] == email
    assert body["verification_required"] is True

    # Console fallback contract: link in response.
    assert "verify_url_dev" in body, body
    link = body["verify_url_dev"]
    assert isinstance(link, str)
    assert link.startswith("http://test/signup/verify-email?token="), link
    # Real plaintext token is in the URL — long, URL-safe, non-empty.
    assert len(link.split("token=", 1)[1]) >= 16

    # send_email is still invoked through the email pipeline (the console
    # backend prints to stdout in dev). The fallback adds the response
    # field + structured log on TOP of that — it doesn't gate the send.
    assert len(captured) == 1
    assert captured[0]["to"] == email


# ---------------------------------------------------------------------------
# 2) SMTP configured → verify_url_dev absent + send_email called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_omits_verify_url_dev_when_smtp_configured(client, db, monkeypatch):
    _patch_smtp_configured(monkeypatch, configured=True)
    captured = _patch_send_email(monkeypatch)

    email = f"smtp-on-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["verification_required"] is True
    assert "verify_url_dev" not in body, (
        "verify_url_dev must NOT leak when SMTP is configured "
        "(would defeat the whole point of the email-only verification flow)"
    )

    # send_email was called exactly once for the signup mail.
    assert len(captured) == 1
    assert captured[0]["to"] == email
    assert "verify" in captured[0]["body"].lower()


# ---------------------------------------------------------------------------
# 3) Structured log entry written when SMTP not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_logs_verify_link_when_smtp_unconfigured(client, db, monkeypatch, capsys):
    """We log the verify URL + 8-char token prefix (NOT the full token).

    The project's logging stack is structlog with a ``PrintLoggerFactory``
    writing straight to stdout (see ``utils/logging.py``), so we capture
    via ``capsys`` rather than ``caplog``. We assert on the rendered KV
    pairs the operator runbook expects to grep for.
    """
    _patch_smtp_configured(monkeypatch, configured=False)
    _patch_send_email(monkeypatch)

    email = f"log-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    link = r.json()["verify_url_dev"]
    full_token = link.split("token=", 1)[1]

    out = capsys.readouterr().out
    # Find the line carrying our event.
    matching_lines = [ln for ln in out.splitlines() if "signup.verify_link_dev" in ln]
    assert matching_lines, (
        f"No 'signup.verify_link_dev' log line in stdout. Captured stdout:\n{out}"
    )
    line = matching_lines[0]

    # Structured KV pairs the runbook documents.
    assert f"email={email}" in line, line
    assert f"verify_url={link}" in line, line
    # Token prefix is the first 8 chars + ellipsis. The full token DOES
    # appear inside ``verify_url=`` (that's the point — operator copies
    # the URL), but it MUST NOT appear under the ``token_prefix=`` key.
    assert f"token_prefix={full_token[:8]}..." in line, line
    # And these signals tell ops *why* the fallback fired.
    assert "smtp_user_set=False" in line, line
    assert "email_backend=console" in line, line
