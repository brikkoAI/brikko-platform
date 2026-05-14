"""Management API — authentication endpoints.

Mounted under ``/v1/auth``. Eight endpoints, all OpenAI-style envelopes on
errors, cookie-based sessions for state. The endpoints expect a SPA on a
trusted origin (CSRF is enforced via ``X-Requested-With`` on the
session-protected POSTs in ``session_middleware.py``); these auth
endpoints are intentionally exempt because the cookies aren't yet set
(signup) or are being set/cleared.

Lifecycle:

    /signup → /verify-email → /login (cookies issued)
                                ↓
                         /change-password / /logout / /refresh
                         /forgot-password → /reset-password

Welcome credit (200 ₽) is granted *atomically* in /verify-email guarded
by a UNIQUE PRIMARY KEY on ``welcome_credits_log.email_hash`` — that's
how we prevent the obvious "delete + signup again" abuse without keeping
the email in plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.cookies import (
    clear_session_cookies,
    set_session_cookies,
)
from voltari_gateway.auth.csrf import (
    attach_csrf_cookie,
    clear_csrf_cookie,
    generate_csrf_token,
)
from voltari_gateway.auth.email_verification import (
    generate_password_reset_token,
    generate_verification_token,
    hash_token,
    verify_password_reset_token,
    verify_verification_token,
)
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.password import (
    MAX_PASSWORD_LEN,
    MIN_PASSWORD_LEN,
    hash_password,
    verify_password,
)
from voltari_gateway.auth.rate_limit import (
    check_email_resend,
    check_forgot,
    check_login,
    check_password_change,
    check_signup,
    check_totp_locked,
    clear_totp_failures,
    record_totp_failure,
)
from voltari_gateway.auth.session import (
    create_access_token,
    create_pre_auth_token,
    create_refresh_token,
    is_refresh_token_active,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    verify_pre_auth_token,
    verify_refresh_token,
)
from voltari_gateway.auth.session_middleware import SessionPrincipal, require_session
from voltari_gateway.auth.sessions_store import (
    mark_session_revoked,
    record_session,
    revoke_all_other_sessions,
)
from voltari_gateway.auth.totp import (
    decrypt_secret,
    is_code_replay,
    mark_code_used,
    verify_recovery_code,
    verify_totp,
)
from voltari_gateway.billing.anonymize_billing import ANONYMIZE_WELCOME_BONUS_KOPECKS
from voltari_gateway.config import AppEnv, get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    Transaction,
    TransactionKind,
    User,
    WelcomeCreditsLog,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.email.client import (
    build_frontend_link,
    is_smtp_configured,
    render_template,
    send_email,
)
from voltari_gateway.utils.errors import (
    GatewayError,
    authentication_error,
    invalid_request,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["auth"])

# Welcome bonus per BRIEF.md §7.
WELCOME_CREDIT_KOPECKS = 20_000


# ---------------------------------------------------------------------------
# Pydantic schemas — request bodies
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=MAX_PASSWORD_LEN)
    # Sprint 11 — acquisition / UTM (Alembic 0013). Frontend resolves
    # cookies (``brikko_utm_*``) → posts here on signup. All four
    # fields are optional: legacy SPA versions and direct API calls
    # without UTM still succeed (the Account row gets NULL).
    # Length caps mirror the column widths so we don't truncate-silently.
    acquisition_channel: str | None = Field(default=None, max_length=32)
    utm_source: str | None = Field(default=None, max_length=64)
    utm_medium: str | None = Field(default=None, max_length=64)
    utm_campaign: str | None = Field(default=None, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=8, max_length=4096)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=MAX_PASSWORD_LEN)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=MAX_PASSWORD_LEN)
    # If 2FA is enabled the dashboard must include the current TOTP code.
    totp_code: str | None = Field(default=None, min_length=6, max_length=6, pattern=r"^\d{6}$")


class Login2FARequest(BaseModel):
    """Body of POST /v1/auth/login/2fa.

    Carries the pre-auth token (issued by /login when totp_enabled=True)
    and exactly one of ``code`` (TOTP) or ``recovery_code``.
    """

    pre_auth_token: str = Field(min_length=8, max_length=4096)
    code: str | None = Field(default=None, min_length=6, max_length=6, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class EmailVerifyResendRequest(BaseModel):
    email: EmailStr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_email(raw: str) -> str:
    """Lower-case + strip — matches DB-side comparison rules."""
    return raw.strip().lower()


def _email_hash(email: str) -> str:
    return hashlib.sha256(_norm_email(email).encode("utf-8")).hexdigest()


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    """Best-effort client IP. ``X-Forwarded-For`` first chain element,
    falling back to socket address. Tests pass ``127.0.0.1``.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "0.0.0.0"


def _require_redis() -> Redis:
    """Resolve the module-level Redis or fail loudly.

    The cookie/session layer is hard-coupled to Redis (whitelist, revocation).
    A missing Redis isn't recoverable for these endpoints — fail fast with
    503 rather than silently let a stolen token live until JWT exp.
    """
    r = get_redis()
    if r is None:
        raise GatewayError(
            status_code=503,
            message="Session store unavailable.",
            type="api_error",
            code="redis_unavailable",
        )
    return r


def _rate_limit_error(retry_after_seconds: int) -> GatewayError:
    return GatewayError(
        status_code=429,
        message="Too many requests. Slow down and try again shortly.",
        type="invalid_request_error",
        code="rate_limited",
        headers={"Retry-After": str(retry_after_seconds)},
    )


async def _get_primary_account(db: AsyncSession, user_id: uuid.UUID) -> Account | None:
    """Fetch the account a user *owns* (the primary).

    Seat-based access is out of scope for /login — we only mint a session
    against an account the user owns. Multi-account selection is V2.
    """
    result = await db.execute(
        select(Account)
        .where(Account.owner_id == user_id)
        .order_by(Account.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _send_template_email(*, to: str, subject: str, template: str, **ctx: object) -> None:
    """Render + send. Logs but never raises — failures don't roll back DB."""
    try:
        body = render_template(template, **ctx)
    except Exception as exc:
        log.error("email_render_failed", template=template, error=str(exc))
        return
    await send_email(to=to, subject=subject, body=body)


# ---------------------------------------------------------------------------
# 0) GET /csrf — bootstrap the double-submit token (FE P0-5)
# ---------------------------------------------------------------------------


@router.get(
    "/csrf",
    status_code=200,
    tags=["auth"],
    summary="Mint a fresh CSRF double-submit token",
    description=(
        "Returns a 64-char URL-safe token in the JSON body and writes the "
        "matching ``vlt_csrf`` cookie. The SPA echoes the token via the "
        "``X-CSRF-Token`` header on every mutating cookie-auth request; the "
        "server compares it to the cookie value (constant time)."
    ),
)
async def csrf_bootstrap(response: Response) -> dict[str, str]:
    """Mint a fresh CSRF token: write it to the cookie + return it in JSON.

    The SPA reads the token from the JSON body (the ``vlt_csrf`` cookie
    is also set, with ``HttpOnly=false``, so an alternative is to read
    it client-side via ``document.cookie``). On every mutating request
    the SPA echoes the token in the ``X-CSRF-Token`` header; the
    server requires header == cookie via constant-time compare.

    Token rotation: each call mints a new token. Calling this on app
    bootstrap, after every login, and after every refresh keeps the
    SPA's in-memory copy fresh and lets the server invalidate the
    token at logout by simply clearing the cookie.

    Public endpoint (no auth required) — token by itself does not
    authenticate; it just makes cross-origin matching impossible. The
    valid auth combination is ``vlt_access`` cookie + matching CSRF
    pair.
    """
    token = generate_csrf_token()
    attach_csrf_cookie(response, token)
    return {"csrf_token": token}


# ---------------------------------------------------------------------------
# 1) POST /signup
# ---------------------------------------------------------------------------


@router.post(
    "/signup",
    status_code=200,
    tags=["auth"],
    summary="Create user account",
    description=(
        "Creates a User + primary Account. Sends a verification email "
        "(plaintext token only in the email; hash stored in DB). The account "
        "cannot ``login`` until the email is verified. No cookies are issued "
        "here — ``/login`` is a separate explicit step."
    ),
    responses={
        200: {"description": "User created; verification email queued."},
        409: {"description": "Email already in use."},
        429: {"description": "Per-IP signup rate limit exceeded."},
    },
)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Create a fresh user + primary account.

    Emits a verification email; the account is not usable for /login until
    the token is verified. No cookies are issued here — login is a separate
    explicit step (CEO 29.04 decision).
    """
    ip = _client_ip(request)
    if not check_signup(ip):
        raise _rate_limit_error(retry_after_seconds=60)

    email = _norm_email(payload.email)

    # Anti-enumeration relaxed here per task spec — UX win > info leak risk
    # (an attacker can already probe via /forgot-password without leaking).
    existing = await db.execute(select(User.id).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise GatewayError(
            status_code=409,
            message="An account with this email already exists.",
            type="invalid_request_error",
            code="email_taken",
            param="email",
        )

    try:
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            email_verified=False,
        )
    except ValueError as exc:
        # Hash function rejects out-of-range passwords.
        raise invalid_request(str(exc), param="password", code="invalid_password") from exc

    db.add(user)
    await db.flush()  # populate user.id

    # Plaintext token goes in the email; hash goes in the DB.
    token_plain = generate_verification_token(user.id, email)
    user.verification_token = hash_token(token_plain)
    user.verification_sent_at = datetime.now(UTC)

    account = Account(
        owner_id=user.id,
        name=email.split("@")[0] or "Personal",
        # Welcome bonus for the /v1/anonymize pay-per-use scheme (BRIEF v2,
        # CEO 2026-05-14). Granted at account creation, not at verify-email,
        # so the balance is visible to the client the moment they create an
        # API key. The pre-existing 200 ₽ welcome lives on /verify-email
        # and is recorded separately — see _credit_welcome_once + the
        # ``welcome_anonymize`` Transaction row below.
        balance_kopecks=ANONYMIZE_WELCOME_BONUS_KOPECKS,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        # Sprint 11 — acquisition / UTM (Alembic 0013). All four are
        # nullable; we trust the frontend's cookie → channel mapping
        # and never normalise here.
        acquisition_channel=payload.acquisition_channel,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
    )
    db.add(account)
    await db.flush()  # populate account.id for the transaction below

    # Ledger row for the anonymize welcome bonus. Separate ref_id from the
    # verify-email welcome 200 ₽ so the two never collide on the UNIQUE
    # (account_id, ref_id) index. ``meta.kind`` tags the row for analytics.
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=ANONYMIZE_WELCOME_BONUS_KOPECKS,
            ref_id=f"welcome-anonymize:{account.id}",
            meta={"kind": "welcome_anonymize"},
        )
    )

    await db.commit()
    await db.refresh(user)
    await db.refresh(account)

    # Send-after-commit. If the email blows up the user can request a resend.
    # NB: link goes to the SPA route /signup/verify-email (token landing page),
    # NOT to the bare /verify-email which is a different SPA route (resend-only,
    # no token handling) and would 404 the user-flow.
    link = build_frontend_link("/signup/verify-email", token=token_plain)

    settings = get_settings()
    smtp_ok = is_smtp_configured(settings)

    response: dict[str, object] = {
        "user_id": str(user.id),
        "email": user.email,
        "verification_required": True,
    }

    # Always dispatch through the email pipeline. With EMAIL_BACKEND=smtp
    # this hits the real relay; with EMAIL_BACKEND=console it prints to
    # stdout; with smtp configured-but-broken it logs a warning and
    # returns. Cheap and keeps the existing console-backend tests stable.
    await _send_template_email(
        to=email,
        subject="Подтверди email — Brikko",
        template="verify-email.txt",
        link=link,
    )

    # Console fallback for the "SMTP not yet wired" window.
    # Triggered by missing SMTP_USER / SMTP_HOST or EMAIL_BACKEND=console
    # — anything that means the user did not get a real email — so the
    # operator can recover the verify URL from logs (journalctl) and,
    # in non-production envs (or when explicitly opted in via
    # ``EXPOSE_DEV_VERIFY_URL``), the SPA can show it inline. Real prod
    # with SMTP wired stays clean: no extra log, no extra response field.
    if not smtp_ok:
        log.info(
            "signup.verify_link_dev",
            email=email,
            verify_url=link,
            token_prefix=token_plain[:8] + "...",
            email_backend=settings.email_backend,
            smtp_host_set=bool(settings.smtp_host),
            smtp_user_set=bool(settings.smtp_user),
        )
        if settings.app_env != AppEnv.PRODUCTION or settings.expose_dev_verify_url:
            response["verify_url_dev"] = link

    return response


# ---------------------------------------------------------------------------
# 2) POST /verify-email
# ---------------------------------------------------------------------------


@router.post(
    "/verify-email",
    status_code=200,
    tags=["auth"],
    summary="Verify email and credit welcome bonus",
    description=(
        "Verifies the user's email using the token sent by ``/signup``. On "
        "success the welcome 200 ₽ credit is granted **once** per email "
        "(anti-abuse: ``welcome_credits_log`` PK is the email hash, not the "
        "user_id, so delete-and-resignup with the same email cannot re-grant)."
    ),
    responses={
        200: {"description": "Email verified; bonus credited if first time."},
        400: {"description": "Token is invalid, expired, or already used."},
    },
)
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Mark the user verified and (idempotently) credit the welcome 200 ₽."""
    parsed = verify_verification_token(payload.token)
    if parsed is None:
        raise invalid_request(
            "Verification token is invalid or expired.",
            param="token",
            code="invalid_token",
        )
    user_id, email = parsed

    user = await db.get(User, user_id)
    if user is None:
        raise invalid_request(
            "Verification token is invalid or expired.",
            param="token",
            code="invalid_token",
        )

    # Hash compare — defends against a leaked DB row by themselves not being
    # enough to verify; the plaintext token still has to come from email.
    # ``compare_digest`` keeps the comparison constant-time so the wire
    # latency can't reveal whether the hash differs in the first byte vs the
    # last byte.
    expected_hash = hash_token(payload.token)
    stored_hash = user.verification_token
    hash_matches = stored_hash is not None and hmac.compare_digest(stored_hash, expected_hash)
    if stored_hash is not None and not hash_matches:
        # Stored hash doesn't match what we just received — token already
        # consumed or rotated. Treat as already-verified: idempotent.
        if user.email_verified:
            return {"verified": True, "welcome_credit_kop": 0}
        raise invalid_request(
            "Verification token is invalid or expired.",
            param="token",
            code="invalid_token",
        )

    if not user.email_verified:
        user.email_verified = True
        user.verification_token = None  # consume — can't be reused.

    # --- Atomic welcome credit -------------------------------------------
    # Insert into the dedup log first; on conflict we know we already paid.
    granted = await _try_grant_welcome_credit(
        db=db,
        user=user,
        email=email,
        ip=_client_ip(request),
    )

    await db.commit()

    return {
        "verified": True,
        "welcome_credit_kop": WELCOME_CREDIT_KOPECKS if granted else 0,
    }


async def _try_grant_welcome_credit(
    *,
    db: AsyncSession,
    user: User,
    email: str,
    ip: str | None,
) -> bool:
    """Insert into ``welcome_credits_log`` (PK = email_hash). On conflict,
    return False without crediting. Otherwise, top up the primary account
    by ``WELCOME_CREDIT_KOPECKS`` and write a Transaction row.

    Returns True iff this call actually credited.
    """
    eh = _email_hash(email)
    ih = _ip_hash(ip)

    # Use INSERT…ON CONFLICT semantics where available; on SQLite (tests) we
    # detect the IntegrityError instead. Both paths are atomic against
    # concurrent verify calls because email_hash is the table's PK.
    try:
        await db.execute(
            insert(WelcomeCreditsLog).values(
                email_hash=eh,
                ip_hash=ih,
                granted_at=datetime.now(UTC),
            )
        )
        await db.flush()
    except IntegrityError:
        # Another verify call won the race or this email was previously
        # credited even after delete-and-resignup.
        await db.rollback()
        # The user.email_verified update happened in the same Session that
        # we just rolled back — re-attach and re-set so the verify flag
        # still persists. We're inside a single txn; the rollback wiped
        # everything including the email_verified=true update.
        merged_user = await db.merge(user)
        merged_user.email_verified = True
        merged_user.verification_token = None
        return False

    # Top up account + transaction row.
    account = await _get_primary_account(db, user.id)
    if account is None:
        # Should never happen — signup guarantees one account. Log & bail
        # without raising, the verify itself succeeded.
        log.error("welcome_credit_no_account", user_id=str(user.id))
        return False

    account.balance_kopecks = (account.balance_kopecks or 0) + WELCOME_CREDIT_KOPECKS
    db.add(
        Transaction(
            account_id=account.id,
            type=TransactionKind.TOPUP,
            amount_kopecks=WELCOME_CREDIT_KOPECKS,
            ref_id=f"welcome:{user.id}",
            meta={"kind": "welcome", "email_hash": eh},
        )
    )
    return True


# ---------------------------------------------------------------------------
# 3) POST /login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    status_code=200,
    tags=["auth"],
    summary="Authenticate and issue session cookies",
    description=(
        "Verifies email/password and issues ``vlt_access`` (15 min) + "
        "``vlt_refresh`` (30 days) HTTP-only cookies, plus a fresh "
        "``vlt_csrf`` cookie + ``csrf_token`` in the body. Subsequent SPA "
        "calls authenticate via these cookies."
    ),
    responses={
        200: {"description": "Login OK; cookies set, csrf_token in body."},
        401: {"description": "Wrong email or password."},
        403: {"description": "Email not verified yet."},
        429: {"description": "Per-IP login rate limit exceeded."},
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Verify password, mint cookies, return account binding info."""
    redis = _require_redis()
    email = _norm_email(payload.email)
    ip = _client_ip(request)

    if not check_login(email, ip):
        raise _rate_limit_error(retry_after_seconds=60)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Constant-time path: even on missing user, run a verify against a
    # dummy hash so timing doesn't reveal account existence.
    if user is None:
        verify_password(payload.password, "$argon2id$v=19$m=65536,t=2,p=2$ZHVtbXk$dummy")
        raise authentication_error("Email or password is incorrect.")

    if not verify_password(payload.password, user.password_hash):
        raise authentication_error("Email or password is incorrect.")

    if not user.email_verified:
        raise GatewayError(
            status_code=403,
            message="Email is not verified yet. Check your inbox for the verification link.",
            type="invalid_request_error",
            code="email_not_verified",
        )

    account = await _get_primary_account(db, user.id)
    if account is None:
        # Edge case: user without a primary account (shouldn't happen post-signup).
        raise GatewayError(
            status_code=403,
            message="No account is associated with this user.",
            type="invalid_request_error",
            code="no_account",
        )
    if account.status != AccountStatus.ACTIVE:
        raise GatewayError(
            status_code=403,
            message="Account is not active.",
            type="invalid_request_error",
            code="account_inactive",
        )

    # 2FA gate — when enabled we DO NOT issue cookies here. Caller has to
    # complete /login/2fa with TOTP / recovery code first.
    if user.totp_enabled:
        pre_auth_token, pre_auth_exp = create_pre_auth_token(user.id)
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="login_password_ok_2fa_required",
            request=request,
        )
        await db.commit()
        return {
            "status": "2fa_required",
            "pre_auth_token": pre_auth_token,
            "expires_at": pre_auth_exp.isoformat(),
        }

    # Mint tokens (no 2FA path).
    access_token, access_exp = create_access_token(user.id, account.id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user.id)

    await set_session_cookies(
        response=response,
        redis=redis,
        user_id=user.id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_exp,
        refresh_jti=refresh_jti,
    )
    # Mirror to Postgres ``sessions`` (for the dashboard list + audit).
    await record_session(
        db,
        user_id=user.id,
        refresh_jti=refresh_jti,
        expires_at=refresh_exp,
        request=request,
    )
    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="login_ok",
        request=request,
        meta={"refresh_jti": refresh_jti},
    )
    await db.commit()

    # Rotate CSRF token on every login. SPA receives the new value in
    # the response body; the cookie is also refreshed so the next
    # mutating request sees a coherent (cookie, header) pair.
    csrf_token = generate_csrf_token()
    attach_csrf_cookie(response, csrf_token)

    return {
        "status": "authenticated",
        "user_id": str(user.id),
        "account_id": str(account.id),
        "expires_at": access_exp.isoformat(),
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# 3b) POST /login/2fa — second factor (Sprint 6)
# ---------------------------------------------------------------------------


@router.post(
    "/login/2fa",
    status_code=200,
    tags=["auth"],
    summary="Complete login by submitting the 2FA factor",
    description=(
        "Accepts a ``pre_auth_token`` (issued by ``/login`` when 2FA is "
        "enabled) plus exactly one of ``code`` (6-digit TOTP) or "
        "``recovery_code``. On success issues the session cookies "
        "exactly like a normal /login response."
    ),
    responses={
        200: {"description": "2FA accepted; cookies issued."},
        400: {"description": "Bad token / missing factor / invalid code."},
        401: {"description": "Pre-auth token expired or factor invalid."},
        429: {"description": "Too many invalid 2FA attempts (lockout)."},
    },
)
async def login_2fa(
    payload: Login2FARequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Verify the second factor and finalise the session."""
    redis = _require_redis()

    claims = verify_pre_auth_token(payload.pre_auth_token)
    if claims is None:
        raise authentication_error("Pre-auth token is invalid or expired.")

    user = await db.get(User, claims.user_id)
    if user is None or not user.totp_enabled:
        raise authentication_error("2FA is not enabled for this user.")

    locked, retry = check_totp_locked(str(user.id))
    if locked:
        raise GatewayError(
            status_code=429,
            message="Too many invalid 2FA codes. Try again later.",
            type="invalid_request_error",
            code="totp_locked",
            headers={"Retry-After": str(retry)},
        )

    if not (payload.code or payload.recovery_code):
        raise invalid_request(
            "Provide either ``code`` (TOTP) or ``recovery_code``.",
            code="totp_or_recovery_required",
        )

    factor_kind = "totp"
    factor_ok = False
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
            account_id=None,
            action="login_2fa_failed",
            outcome="failed",
            request=request,
            meta={"factor": factor_kind},
        )
        await db.commit()
        raise invalid_request(
            "Invalid 2FA code.",
            code="invalid_totp" if factor_kind == "totp" else "invalid_recovery_code",
        )

    # Burn the recovery code if that was the factor used.
    if matched_recovery_hash is not None:
        remaining = [
            h for h in (user.totp_recovery_codes_hashed or []) if h != matched_recovery_hash
        ]
        user.totp_recovery_codes_hashed = remaining

    account = await _get_primary_account(db, user.id)
    if account is None or account.status != AccountStatus.ACTIVE:
        raise GatewayError(
            status_code=403,
            message="Account is not active.",
            type="invalid_request_error",
            code="account_inactive",
        )

    access_token, access_exp = create_access_token(user.id, account.id)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user.id)
    await set_session_cookies(
        response=response,
        redis=redis,
        user_id=user.id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_exp,
        refresh_jti=refresh_jti,
    )
    await record_session(
        db,
        user_id=user.id,
        refresh_jti=refresh_jti,
        expires_at=refresh_exp,
        request=request,
    )
    clear_totp_failures(str(user.id))

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="login_2fa_ok",
        request=request,
        meta={
            "factor": factor_kind,
            "refresh_jti": refresh_jti,
            "recovery_codes_remaining": (
                len(user.totp_recovery_codes_hashed or []) if matched_recovery_hash else None
            ),
        },
    )
    await db.commit()

    csrf_token = generate_csrf_token()
    attach_csrf_cookie(response, csrf_token)

    body: dict[str, object] = {
        "status": "authenticated",
        "user_id": str(user.id),
        "account_id": str(account.id),
        "expires_at": access_exp.isoformat(),
        "csrf_token": csrf_token,
        "factor_used": factor_kind,
    }
    if matched_recovery_hash is not None:
        body["recovery_codes_remaining"] = len(user.totp_recovery_codes_hashed or [])
    return body


# ---------------------------------------------------------------------------
# 4) POST /logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    status_code=200,
    tags=["auth"],
    summary="Revoke session cookies and refresh JTI",
    description=(
        "Clears all session cookies (``vlt_access``, ``vlt_refresh``, "
        "``vlt_csrf``) and revokes the active refresh JTI in Redis so a "
        "stolen token cannot be reused."
    ),
)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    vlt_refresh: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Clear cookies and revoke the current refresh JTI.

    Always returns 200 — logout is idempotent. We don't require an active
    session because we still want a cooperative client to be able to clean
    up after a browser restart, expired token, etc.
    """
    redis = _require_redis()

    user_id: uuid.UUID | None = None
    jti: str | None = None
    if vlt_refresh:
        claims = verify_refresh_token(vlt_refresh)
        if claims is not None:
            user_id = claims.user_id
            jti = claims.jti

    await clear_session_cookies(response, redis=redis, user_id=user_id, refresh_jti=jti)
    clear_csrf_cookie(response)

    # Mirror the revoke into ``sessions`` so the dashboard list doesn't
    # show a phantom row for 30 days.
    if user_id is not None and jti is not None:
        try:
            await mark_session_revoked(db, user_id=user_id, refresh_jti=jti)
            await db.commit()
        except Exception as exc:
            log.warning("logout_session_mirror_failed", error=str(exc))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 5) POST /refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    status_code=200,
    tags=["auth"],
    summary="Rotate access + refresh tokens",
    description=(
        "Validates the current ``vlt_refresh`` cookie's JTI in Redis, "
        "revokes it, and issues a new pair (access + refresh). Returns a "
        "fresh ``csrf_token`` because the SPA's in-memory copy is rotated."
    ),
    responses={
        200: {"description": "Tokens rotated; new cookies set."},
        401: {"description": "Refresh cookie missing, expired, or revoked."},
    },
)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db),
    vlt_refresh: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Rotate the refresh JTI and mint a new access token.

    Standard refresh-rotation: old JTI is revoked the moment we accept it.
    A stolen-then-replayed refresh fails on the second use because the
    JTI is already gone.
    """
    redis = _require_redis()

    if not vlt_refresh:
        raise authentication_error("Missing refresh token.")
    claims = verify_refresh_token(vlt_refresh)
    if claims is None:
        raise authentication_error("Refresh token is invalid or expired.")

    if not await is_refresh_token_active(redis, claims.user_id, claims.jti):
        raise authentication_error("Refresh token has been revoked.")

    user = await db.get(User, claims.user_id)
    if user is None:
        await revoke_refresh_token(redis, claims.user_id, claims.jti)
        raise authentication_error("User no longer exists.")

    account = await _get_primary_account(db, user.id)
    if account is None or account.status != AccountStatus.ACTIVE:
        await revoke_refresh_token(redis, claims.user_id, claims.jti)
        raise authentication_error("Account is not active.")

    # Revoke old JTI before issuing new ones — defends against replay.
    await revoke_refresh_token(redis, claims.user_id, claims.jti)
    # Tombstone the Postgres row too; the new JTI gets its own row below.
    try:
        await mark_session_revoked(db, user_id=user.id, refresh_jti=claims.jti)
    except Exception as exc:
        log.warning("refresh_session_mirror_failed", error=str(exc))

    access_token, access_exp = create_access_token(user.id, account.id)
    new_refresh_token, new_refresh_exp, new_refresh_jti = create_refresh_token(user.id)
    await set_session_cookies(
        response=response,
        redis=redis,
        user_id=user.id,
        access_token=access_token,
        access_expires_at=access_exp,
        refresh_token=new_refresh_token,
        refresh_expires_at=new_refresh_exp,
        refresh_jti=new_refresh_jti,
    )
    await record_session(
        db,
        user_id=user.id,
        refresh_jti=new_refresh_jti,
        expires_at=new_refresh_exp,
    )
    await db.commit()

    # Rotate CSRF on refresh (mirrors login). The SPA reads the new
    # token from the response body and keeps it in memory.
    csrf_token = generate_csrf_token()
    attach_csrf_cookie(response, csrf_token)

    return {
        "user_id": str(user.id),
        "account_id": str(account.id),
        "expires_at": access_exp.isoformat(),
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# 6) POST /forgot-password
# ---------------------------------------------------------------------------


@router.post(
    "/forgot-password",
    status_code=200,
    tags=["auth"],
    summary="Request a password reset link",
    description=(
        "Always returns 200 to avoid email enumeration: success and "
        "wrong-email both look identical to a probing attacker. If the "
        "email maps to a real verified user, a single-use, time-limited "
        "reset token is emailed (HMAC-signed, hash-stored)."
    ),
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Always returns 200, regardless of whether the email exists.

    Anti-enumeration: an attacker can't learn whether an email is registered
    by hitting this endpoint. Per-email rate limit prevents using it as a
    side channel via timing or as a free email-spam tool.
    """
    email = _norm_email(payload.email)

    if not check_forgot(email):
        # Still 200 — don't tell them they're rate-limited (info leak), but
        # also don't bother sending. This is the "act normal, drop silently"
        # branch.
        return {}

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        token_plain = generate_password_reset_token(user.id)
        user.password_reset_token = hash_token(token_plain)
        user.password_reset_sent_at = datetime.now(UTC)
        await db.commit()

        link = build_frontend_link("/reset-password", token=token_plain)
        await _send_template_email(
            to=email,
            subject="Сброс пароля — Brikko",
            template="forgot-password.txt",
            link=link,
        )

    return {}


# ---------------------------------------------------------------------------
# 7) POST /reset-password
# ---------------------------------------------------------------------------


@router.post(
    "/reset-password",
    status_code=200,
    tags=["auth"],
    summary="Set a new password using a reset token",
    description=(
        "Consumes the single-use token issued by ``/forgot-password``. "
        "On success the user's password is replaced and **all** active "
        "refresh JTIs are revoked (any other-device sessions are killed)."
    ),
    responses={
        200: {"description": "Password updated; sessions revoked."},
        400: {"description": "Token invalid, expired, or already used."},
    },
)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Verify the reset token, rotate the password, kill all sessions."""
    redis = _require_redis()

    user_id = verify_password_reset_token(payload.token)
    if user_id is None:
        raise invalid_request(
            "Password-reset token is invalid or expired.",
            param="token",
            code="invalid_token",
        )

    user = await db.get(User, user_id)
    if user is None:
        raise invalid_request(
            "Password-reset token is invalid or expired.",
            param="token",
            code="invalid_token",
        )

    # The DB stores the hash; the plaintext arrived just now. If they don't
    # match, the link was probably superseded by a newer /forgot-password
    # request — treat the older link as invalid. Constant-time compare so a
    # timing oracle can't differentiate "wrong token" from "no such user".
    expected = hash_token(payload.token)
    stored_reset_hash = user.password_reset_token
    if stored_reset_hash is None or not hmac.compare_digest(stored_reset_hash, expected):
        raise invalid_request(
            "Password-reset token is invalid or expired.",
            param="token",
            code="invalid_token",
        )

    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise invalid_request(str(exc), param="new_password", code="invalid_password") from exc

    user.password_reset_token = None
    user.password_reset_sent_at = None
    await db.commit()

    # Force re-login on every device.
    await revoke_all_refresh_tokens(redis, user.id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 8) POST /change-password (session-protected)
# ---------------------------------------------------------------------------


@router.post(
    "/change-password",
    status_code=200,
    tags=["auth"],
    summary="Change password (requires current password + 2FA when enabled)",
    description=(
        "Active session required. Verifies ``old_password``, swaps the "
        "hash and revokes every OTHER device's refresh JTI. The caller's "
        "current session stays alive so the SPA doesn't bounce to /login. "
        "If the account has 2FA enabled, ``totp_code`` is required."
    ),
    responses={
        200: {"description": "Password changed; other refresh sessions revoked."},
        400: {"description": "New password too weak or missing TOTP."},
        401: {"description": "Old password incorrect, TOTP invalid, or session invalid."},
        403: {"description": "Missing CSRF double-submit pair."},
        429: {"description": "Too many failed attempts — try again later."},
    },
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_session),
    vlt_refresh: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Authenticated password change.

    Behaviour change (Sprint 6, Блок 12):

    * Keep the **current** refresh JTI alive (so the SPA doesn't bounce
      to /login) and only revoke other-device sessions.
    * When ``user.totp_enabled`` is True, require a valid ``totp_code``.
    * Rate-limit: 5 failed attempts / hour per user.
    """
    redis = _require_redis()
    user = principal.user

    if not check_password_change(str(user.id)):
        raise GatewayError(
            status_code=429,
            message="Too many password-change attempts. Try again later.",
            type="invalid_request_error",
            code="rate_limited",
            headers={"Retry-After": "3600"},
        )

    if not verify_password(payload.old_password, user.password_hash):
        await write_audit(
            db,
            user_id=user.id,
            account_id=principal.account.id,
            action="password_change_failed",
            outcome="failed",
            request=request,
            meta={"reason": "wrong_password"},
        )
        await db.commit()
        raise authentication_error("Current password is incorrect.")

    # 2FA gate
    if user.totp_enabled:
        if not payload.totp_code:
            raise invalid_request(
                "2FA code is required for password change.",
                param="totp_code",
                code="totp_required",
            )
        locked, retry = check_totp_locked(str(user.id))
        if locked:
            raise GatewayError(
                status_code=429,
                message="Too many invalid 2FA codes. Try again later.",
                type="invalid_request_error",
                code="totp_locked",
                headers={"Retry-After": str(retry)},
            )
        if user.totp_secret_encrypted is None:
            raise GatewayError(
                status_code=500,
                message="2FA configuration is corrupt; contact support.",
                type="api_error",
                code="totp_secret_missing",
            )
        secret_b32 = decrypt_secret(user.totp_secret_encrypted)
        if await is_code_replay(redis, str(user.id), payload.totp_code) or not verify_totp(
            secret_b32, payload.totp_code
        ):
            record_totp_failure(str(user.id))
            await write_audit(
                db,
                user_id=user.id,
                account_id=principal.account.id,
                action="password_change_failed",
                outcome="failed",
                request=request,
                meta={"reason": "invalid_totp"},
            )
            await db.commit()
            raise invalid_request("Invalid 2FA code.", param="totp_code", code="invalid_totp")
        await mark_code_used(redis, str(user.id), payload.totp_code)
        clear_totp_failures(str(user.id))

    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise invalid_request(str(exc), param="new_password", code="invalid_password") from exc

    # Determine the current JTI so we can keep it.
    current_jti: str | None = None
    if vlt_refresh:
        claims = verify_refresh_token(vlt_refresh)
        if claims is not None:
            current_jti = claims.jti

    # Revoke other-device JTIs (Postgres mirror) — collect the list and
    # then delete each whitelist entry in Redis.
    revoked_jtis = await revoke_all_other_sessions(db, user_id=user.id, keep_jti=current_jti)
    for jti in revoked_jtis:
        try:
            await revoke_refresh_token(redis, user.id, jti)
        except Exception as exc:
            log.warning("change_password_redis_revoke_failed", jti=jti, error=str(exc))

    await write_audit(
        db,
        user_id=user.id,
        account_id=principal.account.id,
        action="password_changed",
        request=request,
        meta={"revoked_other_sessions": len(revoked_jtis)},
    )
    await db.commit()

    # Send notification email — best-effort.
    settings_url = build_frontend_link("/app/settings/security")
    try:
        body = render_template(
            "sessions_revoked.txt",
            device="other devices",
            ip_address="-",
            revoked_at=datetime.now(UTC).isoformat(timespec="minutes"),
            revoked_by="изменением пароля",
            settings_security_url=settings_url,
        )
        await send_email(
            to=user.email,
            subject="Пароль изменён — Brikko",
            body=body,
        )
    except Exception as exc:
        log.warning("password_change_email_failed", error=str(exc))

    # Defensive parameter to keep response in scope (cookies stay set).
    _ = response
    return {
        "ok": True,
        "revoked_other_sessions": len(revoked_jtis),
    }


# ---------------------------------------------------------------------------
# 9) POST /verify-email/resend (Sprint 6, Блок 11)
# ---------------------------------------------------------------------------


@router.post(
    "/verify-email/resend",
    status_code=200,
    tags=["auth"],
    summary="Resend the email-verification link",
    description=(
        "Anti-enumeration: always returns 200 regardless of whether the "
        "email maps to an unverified user. Rate-limited to 3 sends per "
        "email per hour."
    ),
    responses={
        200: {"description": "Verification email queued (or silently skipped)."},
        429: {"description": "Per-email re-send rate limit exceeded."},
    },
)
async def verify_email_resend(
    payload: EmailVerifyResendRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Re-send the verification email.

    Behaviour:

    * Always 200 (anti-enumeration; like /forgot-password).
    * 3 sends / hour per email — past that, return 429 with Retry-After.
    * Sends only when the user exists, is not yet verified, and the new
      token actually overrides the old one (we always rotate tokens to
      defend against link-replay).
    """
    email = _norm_email(payload.email)

    if not check_email_resend(email):
        raise GatewayError(
            status_code=429,
            message="Too many resend requests. Try again in an hour.",
            type="invalid_request_error",
            code="rate_limited",
            headers={"Retry-After": "3600"},
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is not None and not user.email_verified:
        token_plain = generate_verification_token(user.id, user.email)
        user.verification_token = hash_token(token_plain)
        user.verification_sent_at = datetime.now(UTC)
        await write_audit(
            db,
            user_id=user.id,
            account_id=None,
            action="email_verify_resent",
            request=request,
        )
        await db.commit()
        # SPA route /signup/verify-email handles the ?token= landing.
        link = build_frontend_link("/signup/verify-email", token=token_plain)
        await _send_template_email(
            to=email,
            subject="Подтверждение email — Brikko",
            template="email_verify_resent.txt",
            link=link,
        )

    return {"ok": True}


__all__ = ["router"]
