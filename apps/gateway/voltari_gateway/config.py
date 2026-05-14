"""Application configuration loaded from environment variables.

We use pydantic-settings for typed env-loading. All settings are read once at
process startup and exposed as a singleton ``get_settings()``.

Security notes
==============

The validators in this module enforce a few invariants that bit us during
the 29.04 self-launch and that we never want to bite us again:

* ``JWT_SECRET`` / ``ENCRYPTION_KEY`` / ``EMAIL_TOKEN_SECRET`` MUST be strong
  in ``staging``/``production``. We refuse to boot with a dev-default, an
  empty string, anything shorter than 32 bytes, or any value containing the
  substrings ``dev``/``change``/``default``/``test``/``example``/``placeholder``
  (case-insensitive). This makes a forgotten ``.env`` fail loudly instead of
  shipping a guessable signing key.
* In ``local``/``test``/``development`` the same checks downgrade to a
  ``warnings.warn(...)`` so the developer ergonomics aren't destroyed
  (you can still run ``pytest`` against the in-tree defaults).
* ``cookie_secure`` is now derived from ``app_env`` by default — the old
  ``cookie_secure=True`` + ``app_env=local`` combo silently dropped
  cookies on HTTP localhost. Explicit ``COOKIE_SECURE`` env var still wins
  if you set it.
"""

from __future__ import annotations

import enum
import warnings
from functools import lru_cache
from typing import Final, Literal

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# AppEnv — the enum we want to be the single source of truth for environment
# ---------------------------------------------------------------------------


class AppEnv(enum.StrEnum):
    """Recognised deployment environments.

    Values are lowercase strings so existing ``APP_ENV=local`` env vars
    keep working unchanged. We accept ``test`` and ``development`` to
    match what CI / Docker usually sets — the old ``Literal[...]`` blew
    up on ``APP_ENV=development`` which we hit on 29.04.
    """

    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        """Return True for envs where we enforce strict secret hygiene."""
        return self in (AppEnv.STAGING, AppEnv.PRODUCTION)

    @property
    def is_dev_like(self) -> bool:
        """Return True for envs that get developer-friendly defaults."""
        return self in (AppEnv.LOCAL, AppEnv.TEST, AppEnv.DEVELOPMENT)


# Substrings we refuse to see inside any production-grade secret.
# All checks are case-insensitive.
_FORBIDDEN_SECRET_SUBSTRINGS: Final[tuple[str, ...]] = (
    "dev",
    "change",
    "default",
    "test",
    "example",
    "placeholder",
    "todo",
)

# Minimum byte length for any secret used to sign a token.
# 32 bytes ≈ 256 bits — matches industry guidance for HMAC-SHA256.
_SECRET_MIN_BYTES: Final[int] = 32


def _validate_secret_strength(
    name: str,
    value: str,
    *,
    app_env: AppEnv,
    allow_empty_in_dev: bool = True,
) -> str:
    """Enforce secret hygiene: length + no dev-default markers.

    In production-like environments any violation raises ``ValueError`` and
    aborts boot. In dev-like environments we ``warnings.warn`` so an
    in-progress test run isn't blocked but the developer still notices.
    """
    raw = value or ""
    lowered = raw.lower()

    # Empty in dev: tolerated only when the caller opted in (encryption_key
    # is sometimes left empty during local prototyping where prompt storage
    # is disabled).
    if not raw:
        if app_env.is_production_like:
            raise ValueError(
                f"{name} must be set in {app_env.value!r} environment "
                f"(must be at least {_SECRET_MIN_BYTES} bytes, no dev defaults)."
            )
        if allow_empty_in_dev:
            return raw
        warnings.warn(
            f"{name} is empty in {app_env.value!r} — fine for prototyping, "
            f"but generation/verification will fail at runtime.",
            stacklevel=3,
        )
        return raw

    too_short = len(raw.encode("utf-8")) < _SECRET_MIN_BYTES
    forbidden_hits = [s for s in _FORBIDDEN_SECRET_SUBSTRINGS if s in lowered]

    if not too_short and not forbidden_hits:
        return raw

    msg_parts: list[str] = []
    if too_short:
        msg_parts.append(f"shorter than {_SECRET_MIN_BYTES} bytes (got {len(raw.encode('utf-8'))})")
    if forbidden_hits:
        msg_parts.append(f"contains forbidden marker(s) {forbidden_hits!r}")
    detail = "; ".join(msg_parts)

    msg = (
        f"{name} fails secret-strength check ({detail}). "
        f"Generate a strong value via:\n"
        f"  python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )

    if app_env.is_production_like:
        raise ValueError(msg)

    warnings.warn(
        f"{msg}\n(downgraded to warning because APP_ENV={app_env.value!r}; "
        f"will be a hard error in staging/production).",
        stacklevel=3,
    )
    return raw


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Top-level configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Brand ---
    brand_name: str = Field(default="Brikko", alias="BRAND_NAME")
    brand_domain: str = Field(default="brikko.local", alias="BRAND_DOMAIN")

    # --- Platform admin (CEO single-pane status, не connect'ится к Account-level
    # ADMIN seat-роли).  Comma-separated email список тех, кому отдаём
    # /v1/account/admin/status.  Пустой список → endpoint всем 403 (закрыт).
    # Email сравнивается без учёта регистра.
    admin_emails: str = Field(default="", alias="ADMIN_EMAILS")

    # --- App ---
    # Accepts AppEnv values; pydantic-settings deserialises strings to enums.
    app_env: AppEnv = Field(default=AppEnv.LOCAL, alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Database ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./voltari.db",
        alias="DATABASE_URL",
    )

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- OpenAI ---
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_timeout_seconds: float = Field(default=60.0, alias="OPENAI_TIMEOUT_SECONDS")

    # --- Anthropic ---
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")
    anthropic_timeout_seconds: float = Field(default=60.0, alias="ANTHROPIC_TIMEOUT_SECONDS")

    # --- Google (Gemini API) ---
    google_api_key: SecretStr = Field(default=SecretStr(""), alias="GOOGLE_API_KEY")
    google_timeout_seconds: float = Field(default=60.0, alias="GOOGLE_TIMEOUT_SECONDS")

    # --- DeepSeek ---
    deepseek_api_key: SecretStr = Field(default=SecretStr(""), alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")
    deepseek_timeout_seconds: float = Field(default=60.0, alias="DEEPSEEK_TIMEOUT_SECONDS")

    # --- Together.ai (Sprint M3, 2026-05-10) ---
    # OpenAI-compatible API for OSS models (Llama, Qwen, Mixtral, FLUX, …).
    # Empty key disables the provider entirely (graceful no-op, like Google).
    together_api_key: SecretStr = Field(default=SecretStr(""), alias="TOGETHER_API_KEY")
    together_base_url: str = Field(default="https://api.together.xyz/v1", alias="TOGETHER_BASE_URL")
    together_timeout_seconds: float = Field(default=60.0, alias="TOGETHER_TIMEOUT_SECONDS")

    # --- Moonshot AI (Kimi) — Sprint M3.2 (2026-05-10) ---
    # OpenAI-compatible chat API for Kimi K2 + moonshot-v1-* models.
    # CEO-payable via UnionPay. International endpoint api.moonshot.ai.
    # Empty key disables the provider entirely.
    moonshot_api_key: SecretStr = Field(default=SecretStr(""), alias="MOONSHOT_API_KEY")
    moonshot_base_url: str = Field(default="https://api.moonshot.ai/v1", alias="MOONSHOT_BASE_URL")
    moonshot_timeout_seconds: float = Field(default=60.0, alias="MOONSHOT_TIMEOUT_SECONDS")

    # --- MiniMax (Hailuo) — Sprint M3.2 (2026-05-10) ---
    # OpenAI-compatible chat API for M-1 / M-Text-01 / abab6.5* models.
    # Note the trailing ``i`` in api.minimaxi.chat — that's MiniMax's
    # actual public domain. Empty key disables the provider.
    minimax_api_key: SecretStr = Field(default=SecretStr(""), alias="MINIMAX_API_KEY")
    minimax_base_url: str = Field(default="https://api.minimaxi.chat/v1", alias="MINIMAX_BASE_URL")
    minimax_timeout_seconds: float = Field(default=60.0, alias="MINIMAX_TIMEOUT_SECONDS")

    # --- Zhipu AI (GLM) — Sprint M3.2 (2026-05-10) ---
    # OpenAI-compatible chat API for GLM-4.5 / GLM-4-Long / GLM-4V-Plus.
    # API key has the form ``<id>.<secret>`` — forwarded verbatim as
    # Bearer (Direct Bearer mode, see ZhipuProvider docstring for the
    # JWT-signed alternative).
    zhipu_api_key: SecretStr = Field(default=SecretStr(""), alias="ZHIPU_API_KEY")
    zhipu_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4", alias="ZHIPU_BASE_URL"
    )
    zhipu_timeout_seconds: float = Field(default=60.0, alias="ZHIPU_TIMEOUT_SECONDS")

    # --- Yandex Foundation Models (reseller-OK pending) ---
    yandex_api_key: SecretStr = Field(default=SecretStr(""), alias="YANDEX_API_KEY")
    yandex_folder_id: str = Field(default="", alias="YANDEX_FOLDER_ID")
    yandex_timeout_seconds: float = Field(default=60.0, alias="YANDEX_TIMEOUT_SECONDS")

    # --- Sber GigaChat (reseller-OK pending) ---
    sber_auth_key: SecretStr = Field(default=SecretStr(""), alias="SBER_AUTH_KEY")
    sber_scope: str = Field(default="GIGACHAT_API_CORP", alias="SBER_SCOPE")
    sber_timeout_seconds: float = Field(default=60.0, alias="SBER_TIMEOUT_SECONDS")

    # --- Outbound HTTP proxy (двухсерверная архитектура, CEO 2026-04-30) ---
    # When set, OpenAI / Anthropic / Google / DeepSeek requests are routed
    # through this proxy URL. Yandex / Sber are NOT routed through it
    # (these are РФ-доступные и не должны выходить за границу).
    #
    # Use case: РФ-сервер хранит auth/billing/PII; зарубежный VPS работает
    # как HTTP-proxy к OpenAI/Anthropic/Google. Связь — WireGuard tunnel.
    # Пустая строка / None = прямые запросы (для dev-окружения).
    #
    # Format: http://10.10.0.1:8080 (WireGuard peer внутри tunnel'а)
    # See: 02_Product/v1.5/16_ceo_decisions_2026-04-30.md «Двухсерверная архитектура»
    outbound_http_proxy: str | None = Field(default=None, alias="OUTBOUND_HTTP_PROXY")

    # --- Crypto ---
    encryption_key: SecretStr = Field(default=SecretStr(""), alias="ENCRYPTION_KEY")
    encryption_key_id: str = Field(default="dev-key", alias="ENCRYPTION_KEY_ID")

    # TOTP secret encryption (Sprint 6, 2FA). Separate from ENCRYPTION_KEY so a
    # leaked request-payload key can't decrypt 2FA secrets and vice versa.
    # 32-byte URL-safe base64-encoded Fernet key in production. In dev/test
    # an empty value falls back to a deterministic key derived from JWT_SECRET
    # so existing tests keep working without env wiring.
    totp_encryption_key: SecretStr = Field(default=SecretStr(""), alias="TOTP_ENCRYPTION_KEY")

    # --- Auth cache ---
    auth_cache_ttl_seconds: int = Field(default=60, alias="AUTH_CACHE_TTL_SECONDS")

    # --- Retention ---
    request_payload_retention_days: int = Field(default=30, alias="REQUEST_PAYLOAD_RETENTION_DAYS")

    # --- ЮKassa ---
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: SecretStr = Field(default=SecretStr(""), alias="YOOKASSA_SECRET_KEY")
    yookassa_webhook_secret: SecretStr = Field(
        default=SecretStr(""), alias="YOOKASSA_WEBHOOK_SECRET"
    )
    # Default True (hard-fail если secret не задан) — это безопасное поведение
    # для production. Временно ставится в False во время bootstrap'а нового
    # магазина в ЮKassa, пока секрет не сгенерирован в ЛК и не положен в env.
    # См. /v1/billing/webhook handler в api/billing.py.
    yookassa_webhook_signature_required: bool = Field(
        default=True, alias="YOOKASSA_WEBHOOK_SIGNATURE_REQUIRED"
    )
    yookassa_return_url_template: str = Field(
        default="https://brikko.ru/billing/return?account={account_id}",
        alias="YOOKASSA_RETURN_URL_TEMPLATE",
    )
    yookassa_base_url: str = Field(default="https://api.yookassa.ru/v3", alias="YOOKASSA_BASE_URL")

    # --- Самозанятый («Мой налог») fallback ---
    npd_enabled: bool = Field(default=True, alias="NPD_ENABLED")
    npd_inn: str = Field(default="", alias="NPD_INN")
    npd_password: SecretStr = Field(default=SecretStr(""), alias="NPD_PASSWORD")

    # --- Autorefill ---
    autorefill_interval_seconds: int = Field(default=300, alias="AUTOREFILL_INTERVAL_SECONDS")
    autorefill_enabled: bool = Field(default=True, alias="AUTOREFILL_ENABLED")

    # --- Management API auth (cookies + JWT + signed tokens) ---
    # JWT_SECRET MUST be set in production. The dev default is intentionally
    # weak so a missing env var fails loudly the moment a real deploy boots.
    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-only-jwt-secret-change-me"), alias="JWT_SECRET"
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256", alias="JWT_ALGORITHM"
    )
    jwt_access_ttl_minutes: int = Field(default=15, alias="JWT_ACCESS_TTL_MINUTES")
    jwt_refresh_ttl_days: int = Field(default=30, alias="JWT_REFRESH_TTL_DAYS")

    # --- OAuth (Sprint pre-M0) ---
    # Comma-separated allow-list of redirect URIs accepted for the
    # ``studio`` first-party OAuth client. Exact match — no glob, no
    # subdomain wildcards. Default covers the docker-compose dev port.
    oauth_studio_redirect_uris: str = Field(
        default="http://localhost:3737/callback",
        alias="OAUTH_STUDIO_REDIRECT_URIS",
    )

    # --- Google + Yandex social login (Sprint M0+1) ---
    # Brikko is the **client** here: we redirect the user to
    # accounts.google.com / oauth.yandex.ru, then exchange the returned
    # code for an access token, fetch userinfo, and link it to a Brikko
    # user. Empty client_id/secret disables the corresponding provider
    # (the /auth/oauth/{provider}/start endpoint returns 503).
    #
    # ``oauth_login_redirect_base`` is the public origin for the
    # callback URL we register with the provider:
    #   https://api.brikko.ru/v1/auth/oauth/google/callback
    # The web SPA sends the user to this gateway URL, not to the SPA;
    # the gateway sets cookies + 302s back to the SPA.
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: SecretStr = Field(
        default=SecretStr(""), alias="GOOGLE_OAUTH_CLIENT_SECRET"
    )
    yandex_oauth_client_id: str = Field(default="", alias="YANDEX_OAUTH_CLIENT_ID")
    yandex_oauth_client_secret: SecretStr = Field(
        default=SecretStr(""), alias="YANDEX_OAUTH_CLIENT_SECRET"
    )
    # Public origin used to assemble redirect URIs registered with the
    # providers. Must NOT carry a trailing slash — we always append
    # ``/v1/auth/oauth/{provider}/callback``.
    oauth_login_redirect_base: str = Field(
        default="http://localhost:8000",
        alias="OAUTH_LOGIN_REDIRECT_BASE",
    )
    # State token TTL (signed HMAC, stateless). 10 min covers slow
    # login flows + a tab the user left open and came back to. Larger
    # is needless attack surface.
    oauth_login_state_ttl_seconds: int = Field(default=600, alias="OAUTH_LOGIN_STATE_TTL_SECONDS")
    # HTTP timeout for the token + userinfo upstream calls.
    oauth_login_http_timeout_seconds: float = Field(
        default=10.0, alias="OAUTH_LOGIN_HTTP_TIMEOUT_SECONDS"
    )
    # Access token TTL for OAuth-issued tokens. Shorter than dashboard
    # session (15 min default) is OK — Studio auto-refreshes silently.
    oauth_access_ttl_minutes: int = Field(default=15, alias="OAUTH_ACCESS_TTL_MINUTES")
    # Refresh TTL for OAuth tokens. 30d matches dashboard.
    oauth_refresh_ttl_days: int = Field(default=30, alias="OAUTH_REFRESH_TTL_DAYS")
    # Authorization-code TTL — RFC 6749 recommends short (≤10 min).
    oauth_code_ttl_seconds: int = Field(default=600, alias="OAUTH_CODE_TTL_SECONDS")

    # Pepper for HMAC-hashing single-use tokens (email-verify, password-reset).
    # See ``voltari_gateway.auth.email_verification.hash_token``.
    # Empty string is allowed in dev (warns); production validator rejects.
    email_token_secret: SecretStr = Field(default=SecretStr(""), alias="EMAIL_TOKEN_SECRET")

    cookie_domain: str = Field(default=".brikko.ru", alias="COOKIE_DOMAIN")
    # ``COOKIE_SECURE`` is now optional. When unset, ``cookie_secure`` is
    # derived from ``app_env`` so dev-on-localhost gets ``Secure=False`` and
    # staging/production get ``Secure=True`` automatically. An explicit env
    # value still wins (``COOKIE_SECURE=false`` for HTTP staging override).
    cookie_secure_override: bool | None = Field(default=None, alias="COOKIE_SECURE")

    cors_origins: str = Field(
        # 2026-05-13 — added app.brikko.ru: dashboard на отдельном поддомене,
        # cross-origin POST падал на CSRF preflight «Disallowed CORS origin»
        # (видно в UI как «Failed to fetch» при создании MCP-токена).
        # Sprint 10 — added brikko.online for the public /v1/models/public
        # endpoint consumed by the marketing landing page (staging domain).
        default="https://brikko.ru,https://app.brikko.ru,https://brikko.online,http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    email_verification_ttl_hours: int = Field(default=24, alias="EMAIL_VERIFICATION_TTL_HOURS")
    password_reset_ttl_minutes: int = Field(default=60, alias="PASSWORD_RESET_TTL_MINUTES")

    # --- Email outbound ---
    email_backend: Literal["console", "smtp"] = Field(default="console", alias="EMAIL_BACKEND")
    email_from: str = Field(default="no-reply@brikko.ru", alias="EMAIL_FROM")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: SecretStr = Field(default=SecretStr(""), alias="SMTP_PASSWORD")
    smtp_starttls: bool = Field(default=True, alias="SMTP_STARTTLS")
    smtp_timeout_seconds: float = Field(default=10.0, alias="SMTP_TIMEOUT_SECONDS")

    # Console fallback: when SMTP is not yet configured (empty SMTP_USER /
    # EMAIL_BACKEND=console), the signup endpoint includes the
    # verification link in the JSON response under ``verify_url_dev``.
    # Auto-on for non-production app_envs; in production stays OFF unless
    # this flag is explicitly set (escape hatch for the brief window
    # between launch and SMTP wiring — see runbooks/smtp_setup_2026-05-01.md).
    expose_dev_verify_url: bool = Field(default=False, alias="EXPOSE_DEV_VERIFY_URL")

    # --- Frontend (for emailed links) ---
    base_url_frontend: str = Field(default="http://localhost:3000", alias="BASE_URL_FRONTEND")

    # --- Auth rate limits (in-memory, single-process) ---
    signup_rate_per_minute: int = Field(default=5, alias="SIGNUP_RATE_PER_MINUTE")
    login_rate_per_minute: int = Field(default=5, alias="LOGIN_RATE_PER_MINUTE")
    forgot_rate_per_hour: int = Field(default=3, alias="FORGOT_RATE_PER_HOUR")
    accept_invite_rate_per_hour: int = Field(default=5, alias="ACCEPT_INVITE_RATE_PER_HOUR")

    # --- Chat rate limits (per-account, Redis token bucket) ---
    # Defaults are PAYG. Tariff overrides applied at request time —
    # see voltari_gateway/middleware/rate_limit.py.
    chat_rate_per_second: int = Field(default=60, alias="CHAT_RATE_PER_SECOND")
    chat_rate_burst: int = Field(default=120, alias="CHAT_RATE_BURST")

    # --- MCP server (Sprint MCP S2) ---
    # Per-token rate limit on /mcp tool-calls. Defends against runaway agent
    # loops in Claude Desktop / Cursor that would otherwise hammer the same
    # endpoint hundreds of times a minute (LLM-driven retries are a real
    # failure mode — Cursor 0.45 had a known bug that called the same tool
    # 200x/min when the model couldn't parse the result).
    #
    # Independent from chat_rate_* because MCP tools are read-only metadata
    # surface (cheap, but should NOT contend with chat-completions buckets).
    # Bucket is 60 req/min default → 1 req/s sustained, burst 60.
    mcp_rate_limit_per_min: int = Field(default=60, alias="MCP_RATE_LIMIT_PER_MIN")
    # Auth-cache TTL for resolved MCP tokens. Same lifetime as api_keys auth
    # cache (60s) — revocations propagate within one window.
    mcp_auth_cache_ttl_seconds: int = Field(default=60, alias="MCP_AUTH_CACHE_TTL_SECONDS")

    # --- Smart Router v2 (Sprint S1, 2026-05-13) ---
    # Global on/off switch for the v2 routing pipeline. CEO 2026-05-13
    # answer to design-doc Q7: ``flag-off prod + per-account admin flip``.
    # Default OFF in production; per-account opt-in is stored in
    # ``accounts.smart_router_v2_enabled`` (Alembic 0022).
    # ``should_use_pipeline()`` returns True iff EITHER this env flag
    # OR the account-level flag is on, so flipping the env flag enables
    # v2 globally without disturbing account-level admin grants.
    # In S1 the pipeline is behaviour-identical to v1 (BaseRouterStage
    # is the only active stage; the other five are NO-OP placeholders),
    # so flipping the flag on staging carries zero regression risk.
    smart_router_v2_enabled: bool = Field(default=False, alias="SMART_ROUTER_V2_ENABLED")

    # --- Circuit breaker ---
    circuit_breaker_threshold: int = Field(default=10, alias="CIRCUIT_BREAKER_THRESHOLD")
    circuit_breaker_window_seconds: int = Field(default=60, alias="CIRCUIT_BREAKER_WINDOW_SECONDS")
    circuit_breaker_reset_seconds: int = Field(default=30, alias="CIRCUIT_BREAKER_RESET_SECONDS")

    # --- Hold janitor ---
    hold_janitor_interval_seconds: int = Field(default=60, alias="HOLD_JANITOR_INTERVAL_SECONDS")
    # Holds older than (expires_at + grace) are eligible for sweep.
    hold_janitor_grace_seconds: int = Field(default=300, alias="HOLD_JANITOR_GRACE_SECONDS")

    # --- Account closure cron (Sprint 7) ---
    # Sweep-frequency for the "30-day grace expired → flip to CLOSED" loop.
    # Hourly is plenty: closure is a slow event class, the user has
    # already been notified, an extra hour isn't material.
    account_closure_interval_seconds: int = Field(
        default=3600, alias="ACCOUNT_CLOSURE_INTERVAL_SECONDS"
    )
    # Sweep-frequency for the "1y after closed_at → anonymise PII" loop.
    # Daily is the natural cadence; nothing else in the system is sub-day
    # sensitive at the 1y horizon.
    account_pii_purge_interval_seconds: int = Field(
        default=86_400, alias="ACCOUNT_PII_PURGE_INTERVAL_SECONDS"
    )

    # --- Data export (Sprint 7) ---
    # Filesystem directory where the async ZIP builder drops archives.
    # In production point this at a persistent volume (or an S3 mount in
    # V2). Default is suitable for local/test only.
    data_export_dir: str = Field(default="./data/exports", alias="DATA_EXPORT_DIR")
    # How long a generated ZIP stays downloadable. After this the cron
    # marks it ``expired`` and the file is deleted from disk.
    data_export_ttl_days: int = Field(default=7, alias="DATA_EXPORT_TTL_DAYS")
    # How often the cleanup cron sweeps for expired exports. Daily is
    # plenty — this is a ~minute-of-disk-IO operation.
    data_export_cleanup_interval_seconds: int = Field(
        default=86_400, alias="DATA_EXPORT_CLEANUP_INTERVAL_SECONDS"
    )

    # --- Seats / invites ---
    invite_ttl_days: int = Field(default=7, alias="INVITE_TTL_DAYS")

    # --- Telegram bot (Sprint 4 Поток M) ---
    # Empty string disables the bot integration entirely. When set,
    # ``main.lifespan`` builds a ``TelegramBotClient`` and exposes it on
    # ``app.state.telegram_bot``.
    telegram_bot_token: SecretStr = Field(default=SecretStr(""), alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field(default="VoltariBot", alias="TELEGRAM_BOT_USERNAME")
    # Shared secret in webhook URL — Telegram posts to
    # ``POST /v1/telegram/webhook?secret=<this>``. Empty disables webhook.
    telegram_webhook_secret: SecretStr = Field(
        default=SecretStr(""), alias="TELEGRAM_WEBHOOK_SECRET"
    )

    # --- FX rates for provider balance normalization (Sprint 14, 2026-05-10) ---
    # Hardcoded rates used by balance_monitor для пересчёта USD/EUR/CNY-балансов
    # в ₽ (для единого runway-индикатора в admin /provider_balances UI).
    # Точность не критична — баланс обновляется руками раз в неделю, ±2% на
    # FX не меняет решения «когда топить».  CEO 2026-04-29: курс USD=80
    # зафиксирован для финмодели.  EUR/CNY округлены до целого ₽.
    # Если CBR курс уедет на 10%+ — обновим вручную (мониторинг через
    # alert «runway_days < 7» от Telegram-bot'а, не нужен auto-FX feed).
    usd_to_rub: int = Field(default=80, alias="USD_TO_RUB")
    eur_to_rub: int = Field(default=88, alias="EUR_TO_RUB")
    cny_to_rub: int = Field(default=11, alias="CNY_TO_RUB")

    # --- Provider balance refresh cron (Sprint 14) ---
    # Раз в 6 часов вызываем balance_monitor.service.refresh_all() для всех
    # API-adapters (DeepSeek, Sber, Moonshot). Manual-balanced провайдеры
    # (OpenAI/Anthropic/...) этим cron'ом не трогаются.
    # 0 → cron выключен (полностью manual). По умолчанию on (21600 = 6 ч).
    provider_balance_refresh_interval_seconds: int = Field(
        default=21_600, alias="PROVIDER_BALANCE_REFRESH_INTERVAL_SECONDS"
    )

    # --- Balance scraper (Phase 2, Sprint 14, 2026-05-11) ---
    # Internal HTTP base of the brikko-scraper container.  When set, the
    # balance_monitor.build_adapters() function returns ``ScrapeRemoteAdapter``
    # for openai/anthropic/together instead of ``ManualAdapter`` — the
    # gateway then forwards balance fetches to the scraper service which
    # runs Playwright on shared encrypted cookies.
    #
    # Empty string → scrapers disabled, Phase 1 behaviour preserved (manual).
    #
    # ``scraper_internal_token`` is the shared secret in ``X-Internal-Token``
    # header.  Generated once at deploy time; both gateway and scraper get
    # the same value in env.  Empty → scrapers also disabled.
    #
    # Format: http://brikko-scraper:9100  (Docker service DNS).
    scraper_url: str = Field(default="", alias="SCRAPER_URL")
    scraper_internal_token: SecretStr = Field(default=SecretStr(""), alias="SCRAPER_INTERNAL_TOKEN")
    scraper_timeout_seconds: float = Field(default=45.0, alias="SCRAPER_TIMEOUT_SECONDS")

    # Filesystem path on the gateway host (inside the gateway container) where
    # encrypted cookie blobs are uploaded by /v1/account/admin/provider_cookies.
    # Volume mount: same /encrypted-cookies as brikko-scraper sees, just
    # mounted in both containers.
    cookies_volume_path: str = Field(default="/encrypted-cookies", alias="COOKIES_VOLUME_PATH")

    # --- Sentry (Sprint 5 / observability) ---
    # Backend error tracking. Empty string disables Sentry entirely (default
    # for local/test). In production we expect a non-empty DSN; absence is a
    # warning at boot but not a hard fail (Sentry-down should not break us).
    # ``release`` defaults to ``__version__`` so each deploy gets a versioned
    # release in the dashboard.
    sentry_dsn: SecretStr = Field(default=SecretStr(""), alias="SENTRY_DSN_GATEWAY")
    # Sample rate for performance traces. 0.0 = errors only, 1.0 = всё.
    # На старте берём 0.05 (5% запросов трассируются) — экономит free-tier.
    sentry_traces_sample_rate: float = Field(default=0.05, alias="SENTRY_TRACES_SAMPLE_RATE")
    # Profiling выключен на free-tier — добавляет только данные в свободном плане
    # Sentry-Developer (5k events/мес — закончатся быстрее).
    sentry_profiles_sample_rate: float = Field(default=0.0, alias="SENTRY_PROFILES_SAMPLE_RATE")

    # --- Prometheus metrics (Sprint 5 / observability) ---
    # /metrics endpoint exposed on app port. Disabled by default in
    # local/test (less noise in pytest); auto-enabled in
    # staging/production via ``_enforce_observability`` validator below.
    metrics_enabled: bool = Field(default=False, alias="METRICS_ENABLED")

    # --- Public Sandbox / Playground (Sprint 12) -----------------------------
    # Anonymous demo endpoint at POST /v1/public/playground. See
    # 02_Product/v1.5/31_public_sandbox_prd_2026-05-01.md.
    #
    # ``sandbox_api_key`` — plaintext API key from a dedicated system-account
    # in api_keys table (created manually by CEO). Backend forwards it as
    # Bearer to the internal gateway pipeline so usage rolls up to that
    # account for COGS reporting. Empty value → endpoint returns 503
    # "Sandbox not configured" (graceful, not 500).
    #
    # ``sandbox_ops_chat_id`` — Telegram chat_id that receives the
    # "budget exhausted" / "halfway through budget" alerts. Empty disables
    # the alert (we still log). Reuses the global TelegramBotClient.
    sandbox_api_key: SecretStr = Field(default=SecretStr(""), alias="SANDBOX_API_KEY")
    sandbox_rate_limit_hour: int = Field(default=5, alias="SANDBOX_RATE_LIMIT_HOUR")
    sandbox_rate_limit_day: int = Field(default=15, alias="SANDBOX_RATE_LIMIT_DAY")
    sandbox_budget_day_kop: int = Field(default=30_000, alias="SANDBOX_BUDGET_DAY_KOP")
    sandbox_max_prompt_length: int = Field(default=500, alias="SANDBOX_MAX_PROMPT_LENGTH")
    sandbox_max_tokens: int = Field(default=150, alias="SANDBOX_MAX_TOKENS")
    sandbox_ops_chat_id: int | None = Field(default=None, alias="SANDBOX_OPS_CHAT_ID")

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("outbound_http_proxy", mode="before")
    @classmethod
    def _coerce_outbound_proxy(cls, v: object) -> object:
        """Treat empty string as ``None`` so ``OUTBOUND_HTTP_PROXY=`` in
        ``.env`` cleanly disables the proxy (instead of httpx trying to use
        an empty URL).
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("app_env", mode="before")
    @classmethod
    def _coerce_app_env(cls, v: object) -> object:
        """Accept legacy strings ``dev``/``prod`` as aliases.

        Pydantic's enum support handles the canonical lowercase string
        forms; here we just normalise a couple of common shorthands so
        ops folks typing ``APP_ENV=prod`` get something sensible instead
        of a validation error.
        """
        if isinstance(v, str):
            normalised = v.strip().lower()
            alias_map = {
                "dev": AppEnv.DEVELOPMENT.value,
                "prod": AppEnv.PRODUCTION.value,
                "stage": AppEnv.STAGING.value,
            }
            return alias_map.get(normalised, normalised)
        return v

    @model_validator(mode="after")
    def _enforce_secret_hygiene(self) -> Settings:
        """Cross-field secret validation.

        Runs after individual fields are populated so ``app_env`` is known
        and we can decide between ``raise`` (prod-like) and ``warn``
        (dev-like).
        """
        env = self.app_env

        # JWT_SECRET — required, never empty (we always sign access tokens).
        _validate_secret_strength(
            "JWT_SECRET",
            self.jwt_secret.get_secret_value(),
            app_env=env,
            allow_empty_in_dev=False,
        )

        # EMAIL_TOKEN_SECRET — required in prod for HMAC token hashing.
        # In dev we tolerate empty (the email-verify code falls back to
        # JWT_SECRET so existing tests keep working — see hash_token).
        _validate_secret_strength(
            "EMAIL_TOKEN_SECRET",
            self.email_token_secret.get_secret_value(),
            app_env=env,
            allow_empty_in_dev=True,
        )

        # ENCRYPTION_KEY — required in prod (used to encrypt request_payloads).
        # Dev tolerates empty: prompt-storage paths short-circuit when no key.
        _validate_secret_strength(
            "ENCRYPTION_KEY",
            self.encryption_key.get_secret_value(),
            app_env=env,
            allow_empty_in_dev=True,
        )
        return self

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cookie_secure(self) -> bool:
        """Effective ``Secure`` flag for session cookies.

        Resolution order:
        1. Explicit ``COOKIE_SECURE`` env var (true/false).
        2. ``True`` for ``staging``/``production``.
        3. ``False`` for ``local``/``test``/``development``.

        This fixes a sharp edge from the old code where ``COOKIE_SECURE=true``
        (default) + ``APP_ENV=local`` (default) caused browsers to silently
        drop cookies on ``http://localhost``, breaking dev login with no
        log message.
        """
        if self.cookie_secure_override is not None:
            return self.cookie_secure_override
        return self.app_env.is_production_like

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed CORS allowlist."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings."""
    return Settings()
