"""Outbound email client.

Two backends:

* ``console`` (default for ``APP_ENV=local``) — print the message to stdout.
  No external dependencies, no surprises in tests, no chance of leaking real
  emails to verification mailboxes during dev.
* ``smtp`` — async SMTP via ``aiosmtplib``. Imported lazily so the dev /
  test environment doesn't require ``aiosmtplib`` to be installed.

Templates live as plain ``.txt`` files under ``email/templates/`` and use
``str.format(**ctx)`` for substitution. Keeping it dumb on purpose: HTML +
Jinja can come in V2, the cost is one ``send_email`` call site change. For
the MVP a plaintext message is exactly what self-employed devs and small
teams expect — looks legitimate, doesn't trip Russian email-provider spam
heuristics, and renders identically in every client.

The module exposes one public function:

    await send_email(to, subject, body)

…and one helper:

    render_template(name, **ctx) -> str

Render is split out so the API layer can ``render_template`` once and pass
the body, instead of re-reading the file on every call.

Errors:
- ``send_email`` returns ``None`` and logs a warning on SMTP failure. We
  never propagate to the request — failed delivery shouldn't 500 a signup.
  Higher tiers (V2) get a small Redis-backed outbox + retry; for MVP the
  user can re-trigger from the UI.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from voltari_gateway.config import Settings, get_settings
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


# --- runtime config probes -------------------------------------------------


def is_smtp_configured(settings: Settings | None = None) -> bool:
    """Return True iff outbound SMTP can actually deliver mail.

    Treats both an explicit ``EMAIL_BACKEND=console`` and an SMTP backend
    that's missing host/credentials as "not configured" — the operator
    intent in either case is "no real email yet". Callers (signup, resend,
    forgot-password) use this to decide whether to surface a console
    fallback (verify-link in API response / structured log) instead of
    silently relying on SMTP that will fail with no visible signal.

    Note: we don't peek into ``smtp_password`` here. Some relays (corporate
    LANs, Postfix without SASL) accept anonymous connections — emptying
    ``SMTP_PASSWORD`` alone shouldn't flip the switch. Empty ``SMTP_USER``,
    on the other hand, is the canonical "not yet wired" signal.
    """
    s = settings if settings is not None else get_settings()
    if s.email_backend != "smtp":
        return False
    if not s.smtp_host:
        return False
    return bool(s.smtp_user)


# --- template rendering ----------------------------------------------------


@lru_cache(maxsize=16)
def _load_template(name: str) -> str:
    """Read template body. Cached for the process lifetime — templates are
    static files shipped with the package; no need to re-read on every send.
    """
    path = _TEMPLATE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"email template not found: {name}")
    return path.read_text(encoding="utf-8")


def render_template(name: str, **ctx: object) -> str:
    """Render a template with ``str.format`` substitution.

    Templates are plaintext. Missing keys raise ``KeyError`` deliberately —
    we want a hard failure in tests, not a half-rendered email going out.
    """
    body = _load_template(name)
    return body.format(**ctx)


# --- backends --------------------------------------------------------------


async def _send_console(to: str, subject: str, body: str) -> None:
    """Dev / test backend — print to stdout in a parseable format."""
    # Single print() call so test code can capture it as one block. Padding
    # with a separator line so multi-email tests are easy to read by eye.
    print(
        f"----- [EMAIL] -----\nTo: {to}\nSubject: {subject}\n\n{body}\n-------------------",
        flush=True,
    )


async def _send_smtp(to: str, subject: str, body: str) -> None:
    """Production backend — aiosmtplib STARTTLS."""
    settings = get_settings()
    if not settings.smtp_host:
        log.warning("smtp_not_configured", reason="empty SMTP_HOST")
        return

    # Lazy import — aiosmtplib only required when EMAIL_BACKEND=smtp.
    try:
        from email.message import EmailMessage

        import aiosmtplib
    except ImportError as exc:
        log.error("aiosmtplib_missing", error=str(exc))
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    # Implicit TLS на порту 465 (SMTPS), STARTTLS на 587/25.
    # Yandex 360 / Gmail / большинство провайдеров используют 465 с implicit TLS.
    # Если бы пытались STARTTLS на 465, сервер ждал бы plain banner до TLS,
    # клиент — TLS-handshake → deadlock 30 сек до timeout (инцидент 2026-05-02).
    use_implicit_tls = settings.smtp_port == 465
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password.get_secret_value() or None,
            use_tls=use_implicit_tls,
            start_tls=settings.smtp_starttls and not use_implicit_tls,
            timeout=settings.smtp_timeout_seconds,
        )
    except Exception as exc:
        log.warning("smtp_send_failed", to=to, error=str(exc))


# --- public API ------------------------------------------------------------


async def send_email(to: str, subject: str, body: str) -> None:
    """Dispatch one email through the configured backend.

    Always returns None. Failures are logged, never raised — the caller
    has already committed the DB row that depends on this email being sent.
    """
    settings = get_settings()
    if not to:
        log.warning("send_email_empty_to")
        return

    if settings.email_backend == "console":
        await _send_console(to, subject, body)
        return

    # smtp — schedule with timeout to avoid pinning a request thread on a
    # slow upstream. ``_send_smtp`` already swallows exceptions but we wrap
    # in wait_for so a hung TCP handshake can't pin us forever either.
    try:
        await asyncio.wait_for(
            _send_smtp(to, subject, body),
            timeout=settings.smtp_timeout_seconds + 5.0,
        )
    except TimeoutError:
        log.warning("smtp_timeout", to=to)


# --- helper: build links ---------------------------------------------------


def build_frontend_link(path: str, **params: str) -> str:
    """Compose a frontend URL of the form ``{BASE_URL_FRONTEND}{path}?k=v``.

    Encodes nothing — caller is expected to pass safe path + values
    (signed tokens are url-safe by construction).
    """
    settings = get_settings()
    base = settings.base_url_frontend.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
    if qs:
        return f"{base}{path}?{qs}"
    return f"{base}{path}"


__all__ = [
    "build_frontend_link",
    "is_smtp_configured",
    "render_template",
    "send_email",
]
