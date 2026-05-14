"""OAuth2 Authorization Code + PKCE endpoints — Studio onboarding.

Routes mounted under ``/v1/oauth``:

    GET  /authorize        — render consent page (requires session cookie).
    POST /authorize        — process consent decision, redirect with code or error.
    POST /token            — exchange code for access/refresh, or refresh→access.
    POST /revoke           — revoke an access or refresh token (RFC 7009).

The dashboard session cookie (vlt_access) authenticates the human at the
consent step; the resulting OAuth tokens are a SEPARATE credential
(distinct ``aud``) used by Studio to call /v1/chat etc.

Per-IP rate limit (5/min) on every endpoint to defend against
code-guessing or token-flooding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.csrf import CSRF_COOKIE, generate_csrf_token, verify_csrf
from voltari_gateway.auth.middleware import get_redis
from voltari_gateway.auth.oauth_codes import (
    OAuthCodeError,
    consume_authorization_code,
    issue_authorization_code,
)
from voltari_gateway.auth.oauth_pkce import PkceError, validate_code_challenge, verify_pkce
from voltari_gateway.auth.oauth_scopes import parse_strict, serialize_scopes
from voltari_gateway.auth.oauth_tokens import (
    create_oauth_access_token,
    create_oauth_refresh_token,
    verify_oauth_access_token,
    verify_oauth_refresh_token,
)
from voltari_gateway.auth.rate_limit import check_oauth
from voltari_gateway.auth.session import (
    is_refresh_token_active,
    register_refresh_token,
    revoke_refresh_token,
    verify_access_token,
)
from voltari_gateway.billing.oauth_welcome_credit import grant_studio_welcome_credit_if_first_time
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import Account, OAuthClient, User
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/oauth", tags=["oauth"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_SCOPE_DESCRIPTIONS = {
    "chat.read": "вызов /v1/chat/completions",
    "messages.read": "вызов /v1/messages",
    "embeddings.read": "вызов /v1/embeddings",
    "audio.read": "вызов /v1/audio/transcriptions",
    "models.read": "вызов /v1/models",
}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "0.0.0.0"


def _rate_limit(request: Request) -> None:
    if not check_oauth(_client_ip(request)):
        raise GatewayError(
            status_code=429,
            message="Too many OAuth requests. Slow down.",
            type="invalid_request_error",
            code="rate_limited",
            headers={"Retry-After": "60"},
        )


def _login_required() -> GatewayError:
    return GatewayError(
        status_code=401,
        message="Login to brikko.ru is required before authorising a client.",
        type="authentication_error",
        code="login_required",
        headers={"X-Login-Required": "1"},
    )


def _form_redirect_error(redirect_uri: str, *, error: str, state: str | None) -> RedirectResponse:
    """Build the OAuth error redirect (RFC 6749 §4.1.2.1)."""
    qs: dict[str, str] = {"error": error}
    if state:
        qs["state"] = state
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(qs)}", status_code=303)


async def _resolve_session_user(
    db: AsyncSession, vlt_access: str | None
) -> tuple[User, Account] | None:
    if not vlt_access:
        return None
    claims = verify_access_token(vlt_access)
    if claims is None:
        return None
    user = await db.get(User, claims.user_id)
    if user is None:
        return None
    account = await db.get(Account, claims.account_id)
    if account is None or account.closed_at is not None:
        return None
    return user, account


async def _load_client_or_400(db: AsyncSession, client_id: str) -> OAuthClient:
    row = await db.get(OAuthClient, client_id)
    if row is None:
        raise invalid_request(
            f"Unknown client_id: {client_id}", code="invalid_client", param="client_id"
        )
    return row


# ---------------------------------------------------------------------------
# GET /authorize — render consent
# ---------------------------------------------------------------------------


@router.get("/authorize", response_class=HTMLResponse)
async def authorize_get(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    vlt_access: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    _rate_limit(request)

    if response_type != "code":
        raise invalid_request(
            "Only response_type=code is supported.", code="unsupported_response_type"
        )
    if code_challenge_method != "S256":
        raise invalid_request("Only S256 PKCE is supported.", code="invalid_request")
    try:
        validate_code_challenge(code_challenge)
    except PkceError as exc:
        raise invalid_request("Malformed code_challenge.", code="invalid_request") from exc

    oauth_client = await _load_client_or_400(db, client_id)
    if redirect_uri not in oauth_client.redirect_uris:
        raise invalid_request(
            "redirect_uri not registered for this client.",
            code="invalid_redirect_uri",
            param="redirect_uri",
        )
    try:
        scopes = parse_strict(scope)
    except ValueError as exc:
        raise invalid_request(str(exc), code="invalid_scope", param="scope") from exc

    # Reject scopes the client wasn't pre-authorised for.
    for s in scopes:
        if s.value not in oauth_client.allowed_scopes:
            raise invalid_request(
                f"Client {client_id!r} cannot request scope {s.value!r}.",
                code="invalid_scope",
                param="scope",
            )

    session = await _resolve_session_user(db, vlt_access)
    if session is None:
        raise _login_required()
    user, account = session

    csrf_token = generate_csrf_token()

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="oauth_authorize_view",
        outcome="ok",
        request=request,
        meta={"client_id": client_id, "scopes": [s.value for s in scopes]},
    )
    await db.commit()

    settings = get_settings()
    response = _TEMPLATES.TemplateResponse(
        request=request,
        name="oauth_consent.html",
        context={
            "client_name": oauth_client.name,
            "client_id": client_id,
            "user_email": user.email,
            "scopes": scopes,
            "scope_string": serialize_scopes(scopes),
            "scope_descriptions": _SCOPE_DESCRIPTIONS,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "csrf_token": csrf_token,
        },
    )
    # Set the matching CSRF cookie so the POST passes the double-submit check.
    # SameSite=lax (not strict) so the OAuth redirect from an external context
    # (Studio CLI popping a browser) still sends the cookie back.
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        max_age=600,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# POST /authorize — consent decision
# ---------------------------------------------------------------------------


@router.post("/authorize")
async def authorize_post(
    request: Request,
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    scope: Annotated[str, Form()],
    state: Annotated[str, Form()],
    code_challenge: Annotated[str, Form()],
    code_challenge_method: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    vlt_access: str | None = Cookie(default=None),
    vlt_csrf: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    _rate_limit(request)

    # Double-submit CSRF: form value must match cookie value.
    verify_csrf(request, csrf_cookie=vlt_csrf, csrf_header=csrf_token)

    oauth_client = await _load_client_or_400(db, client_id)
    if redirect_uri not in oauth_client.redirect_uris:
        raise invalid_request(
            "redirect_uri not registered for this client.",
            code="invalid_redirect_uri",
            param="redirect_uri",
        )
    try:
        scopes = parse_strict(scope)
    except ValueError as exc:
        raise invalid_request(str(exc), code="invalid_scope", param="scope") from exc

    session = await _resolve_session_user(db, vlt_access)
    if session is None:
        raise _login_required()
    user, account = session

    if decision == "deny":
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="oauth_consent_denied",
            outcome="denied",
            request=request,
            meta={"client_id": client_id, "scopes": [s.value for s in scopes]},
        )
        await db.commit()
        return _form_redirect_error(redirect_uri, error="access_denied", state=state)

    if decision != "approve":
        raise invalid_request("decision must be 'approve' or 'deny'.", code="invalid_request")

    # Mint code (10-min single-use, hashed at rest).
    settings = get_settings()
    code_plain = await issue_authorization_code(
        db,
        client_id=client_id,
        user_id=user.id,
        account_id=account.id,
        scopes=[s.value for s in scopes],
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        ttl_seconds=settings.oauth_code_ttl_seconds,
    )

    # Welcome credit on first studio onboarding (idempotent).
    granted = await grant_studio_welcome_credit_if_first_time(
        db,
        account_id=account.id,
        user_id=user.id,
        client_id=client_id,
    )

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="oauth_code_issued",
        outcome="ok",
        request=request,
        meta={
            "client_id": client_id,
            "scopes": [s.value for s in scopes],
            "welcome_credit_granted": granted,
        },
    )
    await db.commit()

    qs = {"code": code_plain, "state": state}
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(qs)}", status_code=303)


# ---------------------------------------------------------------------------
# POST /token — code → tokens, or refresh → tokens
# ---------------------------------------------------------------------------


def _token_error(error: str, *, status: int = 400) -> JSONResponse:
    """OAuth-specific error envelope (RFC 6749 §5.2 — NOT the OpenAI shape)."""
    return JSONResponse(status_code=status, content={"error": error})


@router.post("/token")
async def token_endpoint(
    request: Request,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    _rate_limit(request)

    redis = get_redis()
    settings = get_settings()

    if grant_type == "authorization_code":
        if not (code and redirect_uri and code_verifier):
            return _token_error("invalid_request")

        try:
            stored = await consume_authorization_code(db, code, client_id=client_id)
        except OAuthCodeError:
            return _token_error("invalid_grant")

        if stored.redirect_uri != redirect_uri:
            return _token_error("invalid_grant")

        try:
            verify_pkce(
                verifier=code_verifier,
                challenge=stored.code_challenge,
                method=stored.code_challenge_method,
            )
        except PkceError:
            await db.rollback()
            return _token_error("invalid_grant")

        access_token, _exp, _claims = create_oauth_access_token(
            user_id=stored.user_id,
            account_id=stored.account_id,
            client_id=client_id,
            scopes=stored.scopes,
        )
        refresh_token_str, refresh_exp, refresh_jti = create_oauth_refresh_token(
            user_id=stored.user_id,
            account_id=stored.account_id,
            client_id=client_id,
            scopes=stored.scopes,
        )
        if redis is not None:
            await register_refresh_token(redis, stored.user_id, refresh_jti, refresh_exp)

        await write_audit(
            db,
            user_id=stored.user_id,
            account_id=stored.account_id,
            action="oauth_token_issued",
            outcome="ok",
            request=request,
            meta={"client_id": client_id, "scopes": stored.scopes, "grant_type": grant_type},
        )
        await db.commit()

        return JSONResponse(
            status_code=200,
            content={
                "access_token": access_token,
                "refresh_token": refresh_token_str,
                "token_type": "Bearer",
                "expires_in": settings.oauth_access_ttl_minutes * 60,
                "scope": " ".join(stored.scopes),
            },
        )

    if grant_type == "refresh_token":
        if not refresh_token:
            return _token_error("invalid_request")
        rclaims = verify_oauth_refresh_token(refresh_token)
        if rclaims is None or rclaims.client_id != client_id:
            return _token_error("invalid_grant")
        # Whitelist check — covers logout/revoke.
        if redis is not None and not await is_refresh_token_active(
            redis, rclaims.user_id, rclaims.jti
        ):
            return _token_error("invalid_grant")

        access_token, _exp, _ = create_oauth_access_token(
            user_id=rclaims.user_id,
            account_id=rclaims.account_id,
            client_id=client_id,
            scopes=list(rclaims.scopes),
        )
        new_refresh, refresh_exp, new_jti = create_oauth_refresh_token(
            user_id=rclaims.user_id,
            account_id=rclaims.account_id,
            client_id=client_id,
            scopes=list(rclaims.scopes),
        )
        if redis is not None:
            # Rotate: revoke the old jti, register the new one.
            await revoke_refresh_token(redis, rclaims.user_id, rclaims.jti)
            await register_refresh_token(redis, rclaims.user_id, new_jti, refresh_exp)

        await write_audit(
            db,
            user_id=rclaims.user_id,
            account_id=rclaims.account_id,
            action="oauth_token_refreshed",
            outcome="ok",
            request=request,
            meta={"client_id": client_id},
        )
        await db.commit()

        return JSONResponse(
            status_code=200,
            content={
                "access_token": access_token,
                "refresh_token": new_refresh,
                "token_type": "Bearer",
                "expires_in": settings.oauth_access_ttl_minutes * 60,
                "scope": " ".join(rclaims.scopes),
            },
        )

    return _token_error("unsupported_grant_type")


# ---------------------------------------------------------------------------
# POST /revoke — RFC 7009
# ---------------------------------------------------------------------------


@router.post("/revoke")
async def revoke_endpoint(
    request: Request,
    token: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    _rate_limit(request)
    redis = get_redis()

    # RFC 7009 §2.2: respond 200 even if the token is invalid/unknown.
    rclaims = verify_oauth_refresh_token(token)
    if rclaims is not None and rclaims.client_id == client_id:
        if redis is not None:
            await revoke_refresh_token(redis, rclaims.user_id, rclaims.jti)
        await write_audit(
            db,
            user_id=rclaims.user_id,
            account_id=rclaims.account_id,
            action="oauth_token_revoked",
            outcome="ok",
            request=request,
            meta={"client_id": client_id, "token_type": "refresh"},
        )
        await db.commit()
    else:
        # Treat as access-token best-effort (we have no server-side
        # whitelist for OAuth access tokens — short TTL is the defence).
        aclaims = verify_oauth_access_token(token)
        if aclaims is not None and aclaims.client_id == client_id:
            await write_audit(
                db,
                user_id=aclaims.user_id,
                account_id=aclaims.account_id,
                action="oauth_token_revoked",
                outcome="ok",
                request=request,
                meta={"client_id": client_id, "token_type": "access"},
            )
            await db.commit()

    return JSONResponse(status_code=200, content={})
