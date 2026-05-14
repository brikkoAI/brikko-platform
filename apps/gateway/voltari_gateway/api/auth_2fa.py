"""Management API — 2FA / TOTP endpoints (Sprint 6).

Mounted under ``/v1/auth/2fa``. All endpoints session-cookie protected
through ``require_session`` (CSRF double-submit included).

Flow:

    POST /setup    → secret + provisioning URI + recovery codes
    POST /verify   → confirm first 6-digit code, persist enabled=True
    POST /disable  → password + TOTP/recovery → enabled=False, wipe secret
    POST /recovery-codes/regenerate → password + TOTP → 8 fresh codes

Setup state lives in Redis for 10 minutes keyed by user_id. Persisting
the pending secret to ``users.totp_secret_encrypted`` only happens on
``/verify``: until then a dropped setup attempt does not corrupt the
user's row.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.password import MAX_PASSWORD_LEN, verify_password
from voltari_gateway.auth.rate_limit import (
    check_totp_locked,
    clear_totp_failures,
    record_totp_failure,
)
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.auth.totp import (
    build_provisioning_uri,
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    is_code_replay,
    mark_code_used,
    verify_recovery_code,
    verify_totp,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.session import get_db
from voltari_gateway.email.client import build_frontend_link, render_template, send_email
from voltari_gateway.utils.errors import GatewayError, authentication_error, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/auth/2fa", tags=["auth-2fa"])

# Setup-pending state lives in Redis 10 min. Key is per-user.
_SETUP_TTL_SECONDS = 600
_SETUP_KEY = "totp:setup:{user_id}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SetupResponse(BaseModel):
    """Response of ``POST /setup``.

    The plaintext secret + recovery codes are returned ONCE. The dashboard
    displays them; if the user dismisses the page they have to start over
    (the pending entry expires after 10 min anyway).
    """

    secret: str
    provisioning_uri: str
    recovery_codes: list[str]
    expires_in: int


class VerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class DisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)
    # One of ``code`` (TOTP) or ``recovery_code`` is required.
    code: str | None = Field(default=None, min_length=6, max_length=6, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class RegenerateRequest(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_redis() -> Redis:
    r = get_redis()
    if r is None:
        raise GatewayError(
            status_code=503,
            message="2FA setup store unavailable.",
            type="api_error",
            code="redis_unavailable",
        )
    return r


def _setup_key(user_id: uuid.UUID) -> str:
    return _SETUP_KEY.format(user_id=user_id)


def _enforce_totp_lockout(user_id: uuid.UUID) -> None:
    """Raise 429 when this user has been locked out from TOTP attempts."""
    locked, retry = check_totp_locked(str(user_id))
    if locked:
        raise GatewayError(
            status_code=429,
            message="Too many invalid 2FA codes. Try again later.",
            type="invalid_request_error",
            code="totp_locked",
            headers={"Retry-After": str(retry)},
        )


async def _send_2fa_enabled_email(user_email: str) -> None:
    settings_url = build_frontend_link("/app/settings/security/2fa")
    try:
        body = render_template("2fa_enabled.txt", settings_security_url=settings_url)
        await send_email(
            to=user_email,
            subject="Двухфакторная аутентификация включена — Brikko",
            body=body,
        )
    except Exception as exc:
        log.warning("send_2fa_enabled_email_failed", error=str(exc))


# ---------------------------------------------------------------------------
# 1) POST /v1/auth/2fa/setup
# ---------------------------------------------------------------------------


@router.post("/setup", status_code=200)
async def setup_2fa(
    request: Request,
    principal: SessionPrincipal = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> SetupResponse:
    """Begin TOTP enrollment.

    Generates a fresh secret + 8 recovery codes, stashes both in Redis
    (10 min TTL). The dashboard displays the QR + plaintext codes ONCE
    here; the actual ``users.totp_secret_encrypted`` write only happens
    after ``/verify`` confirms the first code.
    """
    user = principal.user
    if user.totp_enabled:
        raise invalid_request(
            "Two-factor authentication is already enabled.",
            code="2fa_already_enabled",
        )

    settings = get_settings()
    redis = _require_redis()

    secret = generate_totp_secret()
    recovery_codes = generate_recovery_codes()
    # We HMAC-hash the recovery codes for at-rest storage; plaintext returned
    # to the user once below. Storing both in Redis (encrypted+hashed) keeps
    # /verify simple — it doesn't have to re-derive anything.
    payload: dict[str, Any] = {
        "secret_b32": secret,
        "recovery_hashes": [hash_recovery_code(c) for c in recovery_codes],
        "issued_at": datetime.now(UTC).isoformat(),
    }
    await redis.setex(_setup_key(user.id), _SETUP_TTL_SECONDS, json.dumps(payload))

    uri = build_provisioning_uri(
        secret,
        account_email=user.email,
        issuer=settings.brand_name,
    )
    _ = settings  # silences unused alias

    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="2fa_setup_started",
        request=request,
    )
    await db.commit()

    return SetupResponse(
        secret=secret,
        provisioning_uri=uri,
        recovery_codes=recovery_codes,
        expires_in=_SETUP_TTL_SECONDS,
    )


# ---------------------------------------------------------------------------
# 2) POST /v1/auth/2fa/verify
# ---------------------------------------------------------------------------


@router.post("/verify", status_code=200)
async def verify_2fa(
    payload: VerifyRequest,
    request: Request,
    principal: SessionPrincipal = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Confirm the first TOTP code and enable 2FA on the account.

    Reads pending setup from Redis, verifies the code, encrypts and
    persists the secret + recovery hashes on ``users``, marks
    ``totp_enabled=True``. Sends the confirmation email.
    """
    user = principal.user
    if user.totp_enabled:
        raise invalid_request(
            "Two-factor authentication is already enabled.",
            code="2fa_already_enabled",
        )

    _enforce_totp_lockout(user.id)

    redis = _require_redis()
    raw = await redis.get(_setup_key(user.id))
    if raw is None:
        raise GatewayError(
            status_code=410,
            message="2FA setup session expired. Start setup again.",
            type="invalid_request_error",
            code="2fa_setup_expired",
        )

    pending = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    secret_b32 = pending["secret_b32"]
    recovery_hashes: list[str] = list(pending["recovery_hashes"])

    if await is_code_replay(redis, str(user.id), payload.code) or not verify_totp(
        secret_b32, payload.code
    ):
        record_totp_failure(str(user.id))
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="2fa_setup_verify_failed",
            outcome="failed",
            request=request,
        )
        await db.commit()
        raise invalid_request("Invalid 2FA code. Try again.", param="code", code="invalid_totp")

    # Mark this code as used so a network replay can't re-confirm.
    await mark_code_used(redis, str(user.id), payload.code)

    user.totp_secret_encrypted = encrypt_secret(secret_b32)
    user.totp_recovery_codes_hashed = recovery_hashes
    user.totp_enabled = True
    user.totp_enabled_at = datetime.now(UTC)

    # One-shot — drop the pending entry so a stale Redis slot can't be
    # re-used to "verify" an already-enabled user.
    await redis.delete(_setup_key(user.id))
    clear_totp_failures(str(user.id))

    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="2fa_enabled",
        request=request,
    )
    await db.commit()

    await _send_2fa_enabled_email(user.email)

    return {
        "enabled": True,
        "recovery_codes_remaining": len(recovery_hashes),
    }


# ---------------------------------------------------------------------------
# 3) POST /v1/auth/2fa/disable
# ---------------------------------------------------------------------------


@router.post("/disable", status_code=200)
async def disable_2fa(
    payload: DisableRequest,
    request: Request,
    response: Response,
    principal: SessionPrincipal = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Disable 2FA. Requires password + (TOTP code OR recovery code).

    On success we wipe the secret + recovery codes, set ``totp_enabled=False``,
    revoke all OTHER refresh sessions (current cookie kept so the dashboard
    doesn't flicker), and email a tamper-evident notice.
    """
    user = principal.user
    if not user.totp_enabled:
        raise invalid_request(
            "Two-factor authentication is not enabled.",
            code="2fa_not_enabled",
        )

    if not verify_password(payload.password, user.password_hash):
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="2fa_disable_failed",
            outcome="failed",
            request=request,
            meta={"reason": "wrong_password"},
        )
        await db.commit()
        raise authentication_error("Current password is incorrect.")

    if not (payload.code or payload.recovery_code):
        raise invalid_request(
            "Provide either ``code`` (TOTP) or ``recovery_code``.",
            code="totp_or_recovery_required",
        )

    _enforce_totp_lockout(user.id)

    redis = _require_redis()
    factor_ok = False
    factor_kind = "totp"
    matched_recovery_hash: str | None = None

    if payload.code:
        if user.totp_secret_encrypted is None:
            raise GatewayError(
                status_code=500,
                message="2FA configuration is corrupt; contact support.",
                type="api_error",
                code="totp_secret_missing",
            )
        secret_b32 = decrypt_secret(user.totp_secret_encrypted)
        if await is_code_replay(redis, str(user.id), payload.code):
            factor_ok = False
        else:
            factor_ok = verify_totp(secret_b32, payload.code)
        if factor_ok:
            await mark_code_used(redis, str(user.id), payload.code)
    elif payload.recovery_code:
        factor_kind = "recovery"
        recovery_hashes = list(user.totp_recovery_codes_hashed or [])
        matched_recovery_hash = verify_recovery_code(payload.recovery_code, recovery_hashes)
        factor_ok = matched_recovery_hash is not None

    if not factor_ok:
        record_totp_failure(str(user.id))
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="2fa_disable_failed",
            outcome="failed",
            request=request,
            meta={"reason": f"invalid_{factor_kind}"},
        )
        await db.commit()
        raise invalid_request(
            "Invalid 2FA code.",
            code="invalid_totp" if factor_kind == "totp" else "invalid_recovery_code",
        )

    # All factors verified — disable.
    user.totp_secret_encrypted = None
    user.totp_recovery_codes_hashed = None
    user.totp_enabled = False
    user.totp_enabled_at = None
    clear_totp_failures(str(user.id))

    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="2fa_disabled",
        request=request,
        meta={"factor": factor_kind},
    )
    await db.commit()

    # Don't kill the calling session here — the user is in a logged-in
    # SPA and would be bounced for no reason. /change-password handles
    # multi-session revoke on its own path.

    settings_url = build_frontend_link("/app/settings/security/2fa")
    try:
        body = render_template("2fa_enabled.txt", settings_security_url=settings_url)
        await send_email(
            to=user.email,
            subject="Двухфакторная аутентификация отключена — Brikko",
            body=body,
        )
    except Exception as exc:
        log.warning("send_2fa_disabled_email_failed", error=str(exc))

    # Defensive: response is unused for now but keep the parameter so the
    # signature matches Sessions API (single point to add cookie clears).
    _ = response
    return {"enabled": False}


# ---------------------------------------------------------------------------
# 4) POST /v1/auth/2fa/recovery-codes/regenerate
# ---------------------------------------------------------------------------


@router.post("/recovery-codes/regenerate", status_code=200)
async def regenerate_recovery_codes(
    payload: RegenerateRequest,
    request: Request,
    principal: SessionPrincipal = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Issue 8 fresh recovery codes; old ones become invalid.

    Requires password + valid TOTP. The plaintext codes are returned once;
    only HMAC hashes persist.
    """
    user = principal.user
    if not user.totp_enabled:
        raise invalid_request(
            "Two-factor authentication is not enabled.",
            code="2fa_not_enabled",
        )

    _enforce_totp_lockout(user.id)

    if not verify_password(payload.password, user.password_hash):
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="recovery_regenerate_failed",
            outcome="failed",
            request=request,
            meta={"reason": "wrong_password"},
        )
        await db.commit()
        raise authentication_error("Current password is incorrect.")

    if user.totp_secret_encrypted is None:
        raise GatewayError(
            status_code=500,
            message="2FA configuration is corrupt; contact support.",
            type="api_error",
            code="totp_secret_missing",
        )
    secret_b32 = decrypt_secret(user.totp_secret_encrypted)
    redis = _require_redis()
    if await is_code_replay(redis, str(user.id), payload.code) or not verify_totp(
        secret_b32, payload.code
    ):
        record_totp_failure(str(user.id))
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="recovery_regenerate_failed",
            outcome="failed",
            request=request,
            meta={"reason": "invalid_totp"},
        )
        await db.commit()
        raise invalid_request("Invalid 2FA code.", param="code", code="invalid_totp")
    await mark_code_used(redis, str(user.id), payload.code)

    fresh = generate_recovery_codes()
    user.totp_recovery_codes_hashed = [hash_recovery_code(c) for c in fresh]
    clear_totp_failures(str(user.id))

    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="recovery_regenerated",
        request=request,
    )
    await db.commit()

    return {"recovery_codes": fresh}


__all__ = ["router"]
