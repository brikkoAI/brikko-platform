"""FastAPI application entrypoint.

Wiring order on startup:

    1. Configure logging.
    2. Initialise DB engine and Redis.
    3. Mount routers and exception handlers.
    4. Build a single OpenAI provider instance, attach to app.state.

Shutdown reverses 4 → 2.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from voltari_gateway import __version__
from voltari_gateway.api.account import router as account_router
from voltari_gateway.api.account_closure import router as account_closure_router
from voltari_gateway.api.activity import router as activity_router
from voltari_gateway.api.admin_balances import router as admin_balances_router
from voltari_gateway.api.admin_cookies import router as admin_cookies_router
from voltari_gateway.api.admin_smart_router_v2 import (
    router as admin_smart_router_v2_router,
)
from voltari_gateway.api.admin_status import router as admin_status_router
from voltari_gateway.api.analytics import router as analytics_router
from voltari_gateway.api.anonymize import router as anonymize_router
from voltari_gateway.api.audio import router as audio_router
from voltari_gateway.api.auth import router as auth_router
from voltari_gateway.api.auth_2fa import router as auth_2fa_router
from voltari_gateway.api.billing import router as billing_router
from voltari_gateway.api.chat import router as chat_router
from voltari_gateway.api.data_export import router as data_export_router
from voltari_gateway.api.embeddings import router as embeddings_router
from voltari_gateway.api.health import router as health_router
from voltari_gateway.api.images import router as images_router
from voltari_gateway.api.keys import router as keys_router
from voltari_gateway.api.mcp_endpoint import build_mcp_asgi
from voltari_gateway.api.mcp_keys import router as mcp_keys_router
from voltari_gateway.api.messages import router as messages_router
from voltari_gateway.api.models import router as models_router
from voltari_gateway.api.oauth import router as oauth_router
from voltari_gateway.api.oauth_login import router as oauth_login_router
from voltari_gateway.api.playground import router as playground_router
from voltari_gateway.api.public_status import router as public_status_router
from voltari_gateway.api.routing_preferences import router as routing_prefs_router
from voltari_gateway.api.seats import (
    invites_router as account_invites_router,
)
from voltari_gateway.api.seats import (
    seats_router as account_seats_router,
)
from voltari_gateway.api.sessions import router as sessions_router
from voltari_gateway.api.tariff import router as tariff_router
from voltari_gateway.api.telegram import (
    account_link_router as telegram_link_router,
)
from voltari_gateway.api.telegram import (
    webhook_router as telegram_webhook_router,
)
from voltari_gateway.api.traces import router as traces_router
from voltari_gateway.api.usage import router as usage_router
from voltari_gateway.auth.middleware import set_redis
from voltari_gateway.balance_monitor.service import (
    aclose_adapters,
    balance_refresh_loop,
    build_adapters,
)
from voltari_gateway.billing.account_closure_cron import (
    account_closure_loop,
    account_pii_purge_loop,
)
from voltari_gateway.billing.autorefill import autorefill_loop
from voltari_gateway.billing.janitor import hold_janitor_loop
from voltari_gateway.billing.receipts import LknpdClient, LknpdConfig
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.config import get_settings
from voltari_gateway.db.oauth_seed import seed_oauth_clients
from voltari_gateway.db.session import dispose_engine, get_session_factory, init_engine
from voltari_gateway.integrations.telegram_bot import TelegramBotClient
from voltari_gateway.mcp_server.auth import set_mcp_redis
from voltari_gateway.mcp_server.rate_limit import (
    McpRateLimiter,
    set_mcp_rate_limiter,
)
from voltari_gateway.mcp_server.server import build_session_manager
from voltari_gateway.middleware.rate_limit import (
    ChatRateLimiter,
    set_rate_limiter,
)
from voltari_gateway.providers.anthropic_provider import AnthropicProvider
from voltari_gateway.providers.deepseek_provider import DeepSeekProvider
from voltari_gateway.providers.minimax_provider import MiniMaxProvider
from voltari_gateway.providers.moonshot_provider import MoonshotProvider
from voltari_gateway.providers.openai_provider import OpenAIProvider
from voltari_gateway.providers.registry import ProviderRegistry
from voltari_gateway.providers.sber_provider import SberProvider
from voltari_gateway.providers.together_provider import TogetherProvider
from voltari_gateway.providers.yandex_provider import YandexProvider
from voltari_gateway.providers.zhipu_provider import ZhipuProvider
from voltari_gateway.router.catalog import Provider as ProviderEnum
from voltari_gateway.router.circuit_breaker import (
    CircuitBreaker,
    CircuitConfig,
)
from voltari_gateway.router.router import Router
from voltari_gateway.utils.errors import (
    GatewayError,
    gateway_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from voltari_gateway.utils.logging import configure_logging, get_logger
from voltari_gateway.utils.observability import init_sentry, setup_prometheus


async def _cancel_with_timeout(
    task: asyncio.Task[None] | None,
    stop_event: asyncio.Event,
    timeout: float,
) -> None:
    """Signal a background task to stop and wait up to ``timeout`` seconds.

    Mirrors the old inline ``if X is not None: X_stop.set(); try: wait_for
    except TimeoutError: cancel()`` blocks. Factored out so the MCP-server
    composition in ``_lifespan`` doesn't need to indent the entire teardown
    one more level (which made the diff hard to read).
    """
    if task is None:
        return
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        task.cancel()


async def _teardown_background_tasks(
    *,
    autorefill_task: asyncio.Task[None] | None,
    autorefill_stop: asyncio.Event,
    janitor_task: asyncio.Task[None] | None,
    janitor_stop: asyncio.Event,
    closure_task: asyncio.Task[None] | None,
    closure_stop: asyncio.Event,
    pii_purge_task: asyncio.Task[None] | None,
    pii_purge_stop: asyncio.Event,
    data_export_task: asyncio.Task[None] | None,
    data_export_stop: asyncio.Event,
    balance_refresh_task: asyncio.Task[None] | None,
    balance_refresh_stop: asyncio.Event,
) -> None:
    """Cancel every gateway background task. Order matches the previous code."""
    await _cancel_with_timeout(autorefill_task, autorefill_stop, 10.0)
    await _cancel_with_timeout(janitor_task, janitor_stop, 5.0)
    await _cancel_with_timeout(closure_task, closure_stop, 5.0)
    await _cancel_with_timeout(pii_purge_task, pii_purge_stop, 5.0)
    await _cancel_with_timeout(data_export_task, data_export_stop, 5.0)
    await _cancel_with_timeout(balance_refresh_task, balance_refresh_stop, 5.0)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = get_logger("voltari.startup")
    settings = get_settings()

    # Sentry must be initialised first — otherwise startup errors below
    # would not be captured. Safe to init in any environment: no DSN = no-op.
    init_sentry(settings, release=__version__)

    init_engine()
    log.info("engine_ready", database=settings.database_url.split("@")[-1])

    # Seed first-party OAuth clients (idempotent, safe to call on every boot).
    try:
        await seed_oauth_clients(get_session_factory())
    except Exception as exc:  # pragma: no cover — startup must not crash on seed
        log.error("oauth_seed_failed", error=str(exc))

    redis_client: Redis | None = None
    try:
        redis_client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        set_redis(redis_client)
        log.info("redis_ready", url=settings.redis_url)
    except Exception as exc:
        log.warning("redis_unavailable", error=str(exc))
        set_redis(None)
        if redis_client is not None:
            await redis_client.aclose()
            redis_client = None

    # --- Provider Registry: build one adapter per upstream that has creds. ---
    registry = ProviderRegistry()
    if settings.openai_api_key.get_secret_value():
        openai_prov = OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_timeout_seconds,
            outbound_proxy=settings.outbound_http_proxy,
        )
        registry.register(ProviderEnum.OPENAI, openai_prov)
        # Backwards-compat: existing chat tests poke ``app.state.openai_provider``.
        app.state.openai_provider = openai_prov
        log.info("openai_provider_ready")
    if settings.anthropic_api_key.get_secret_value():
        registry.register(
            ProviderEnum.ANTHROPIC,
            AnthropicProvider(
                api_key=settings.anthropic_api_key.get_secret_value(),
                base_url=settings.anthropic_base_url,
                timeout_seconds=settings.anthropic_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            ),
        )
    if settings.google_api_key.get_secret_value():
        try:
            from voltari_gateway.providers.google_provider import GoogleProvider

            registry.register(
                ProviderEnum.GOOGLE,
                GoogleProvider(
                    api_key=settings.google_api_key.get_secret_value(),
                    timeout_seconds=settings.google_timeout_seconds,
                    outbound_proxy=settings.outbound_http_proxy,
                ),
            )
        except RuntimeError as exc:
            log.warning("google_provider_unavailable", error=str(exc))
    if settings.deepseek_api_key.get_secret_value():
        # CEO 2026-04-30: DeepSeek работает из РФ напрямую (китайский провайдер,
        # не блокирует РФ-IP). Не проксируем — экономим latency и упрощаем
        # архитектуру. Если в будущем DeepSeek начнёт блокировать —
        # перевключим: outbound_proxy=settings.outbound_http_proxy.
        registry.register(
            ProviderEnum.DEEPSEEK,
            DeepSeekProvider(
                api_key=settings.deepseek_api_key.get_secret_value(),
                base_url=settings.deepseek_base_url,
                timeout_seconds=settings.deepseek_timeout_seconds,
                outbound_proxy=None,  # direct, no proxy
            ),
        )
    if settings.yandex_api_key.get_secret_value() and settings.yandex_folder_id:
        registry.register(
            ProviderEnum.YANDEX,
            YandexProvider(
                folder_id=settings.yandex_folder_id,
                api_key=settings.yandex_api_key.get_secret_value(),
                timeout_seconds=settings.yandex_timeout_seconds,
            ),
        )
    if settings.sber_auth_key.get_secret_value():
        registry.register(
            ProviderEnum.SBER,
            SberProvider(
                auth_key=settings.sber_auth_key.get_secret_value(),
                scope=settings.sber_scope,
                timeout_seconds=settings.sber_timeout_seconds,
            ),
        )
    # Sprint M3 (2026-05-10) — Together.ai. US-hosted, routed through
    # outbound_http_proxy same as OpenAI/Anthropic/Google. Empty key =
    # graceful no-op (provider not registered, /v1/models still serves
    # Together entries but a chat call would 503 with provider_unavailable).
    if settings.together_api_key.get_secret_value():
        registry.register(
            ProviderEnum.TOGETHER,
            TogetherProvider(
                api_key=settings.together_api_key.get_secret_value(),
                base_url=settings.together_base_url,
                timeout_seconds=settings.together_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            ),
        )
    # Sprint M3.2 (2026-05-10) — Chinese frontier providers (Moonshot,
    # MiniMax, Zhipu). All three OpenAI-compatible, all three CEO-payable
    # via UnionPay. Empty key = graceful no-op: provider not registered,
    # the catalog filter (api/models.py:_provider_configured) hides their
    # models from /v1/models so SDK callers don't see a model they can't
    # actually use yet. Pinning a Moonshot/MiniMax/Zhipu model id while
    # the key is missing 503s with provider_unavailable.
    if settings.moonshot_api_key.get_secret_value():
        registry.register(
            ProviderEnum.MOONSHOT,
            MoonshotProvider(
                api_key=settings.moonshot_api_key.get_secret_value(),
                base_url=settings.moonshot_base_url,
                timeout_seconds=settings.moonshot_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            ),
        )
    if settings.minimax_api_key.get_secret_value():
        registry.register(
            ProviderEnum.MINIMAX,
            MiniMaxProvider(
                api_key=settings.minimax_api_key.get_secret_value(),
                base_url=settings.minimax_base_url,
                timeout_seconds=settings.minimax_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            ),
        )
    if settings.zhipu_api_key.get_secret_value():
        registry.register(
            ProviderEnum.ZHIPU,
            ZhipuProvider(
                api_key=settings.zhipu_api_key.get_secret_value(),
                base_url=settings.zhipu_base_url,
                timeout_seconds=settings.zhipu_timeout_seconds,
                outbound_proxy=settings.outbound_http_proxy,
            ),
        )
    app.state.provider_registry = registry
    router_engine = Router()
    app.state.router_engine = router_engine
    # Sprint S1 — Smart Router v2 pipeline. The pipeline is stateless
    # and stage objects are reused across requests, so we build one
    # instance per process and stash it on app.state. The API handler
    # checks ``should_use_pipeline`` per request; pipeline is only
    # invoked when the env-flag OR the per-account flag is on.
    from voltari_gateway.router.pipeline import RouterPipeline

    app.state.router_pipeline = RouterPipeline(router_engine)
    log.info(
        "provider_registry_ready",
        configured=[p.value for p in registry.configured_providers()],
    )

    # --- Circuit breaker ---
    # Always wired (the breaker degrades to no-op when redis is None).
    app.state.circuit_breaker = CircuitBreaker(
        redis_client,
        config=CircuitConfig(
            threshold=settings.circuit_breaker_threshold,
            window_seconds=settings.circuit_breaker_window_seconds,
            reset_seconds=settings.circuit_breaker_reset_seconds,
        ),
    )

    # --- Chat rate limiter ---
    set_rate_limiter(ChatRateLimiter(redis_client))

    # --- MCP server (Sprint MCP S2) ---
    # Share the same Redis client with the api_keys auth path. The cache
    # namespaces are disjoint (``auth:key:*`` vs ``auth:mcp:*``) so the
    # two surfaces can't trample each other.
    set_mcp_redis(redis_client)
    set_mcp_rate_limiter(
        McpRateLimiter(redis_client, limit_per_min=settings.mcp_rate_limit_per_min)
    )

    # --- PII Natasha warm-up (Sprint 13 / Privacy v2 — Task 2.4b) ---
    # Cold-load the Natasha NER + pymorphy2 + yargy stack once at startup
    # (~1.5 s, ~250 MB).  Subsequent `find_persons` calls in PII masking
    # hot path then skip the bootstrap.  Failure is non-fatal: if the
    # models can't load (corrupt cache, missing OS deps), masking falls
    # back to regex-only categories and PERSON detection silently degrades.
    try:
        from voltari_gateway.pii.ru_person import warm_up as _pii_warm_up

        _pii_warm_up()
        log.info("pii_natasha_warmed_up")
    except Exception as exc:
        log.warning("pii_natasha_warm_up_failed", error=str(exc))

    # --- ЮKassa ---
    yookassa_client: YooKassaClient | None = None
    if settings.yookassa_shop_id and settings.yookassa_secret_key.get_secret_value():
        yk_config = YooKassaConfig(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key.get_secret_value(),
            webhook_secret=settings.yookassa_webhook_secret.get_secret_value(),
            return_url_template=settings.yookassa_return_url_template,
            base_url=settings.yookassa_base_url,
        )
        yookassa_client = YooKassaClient(yk_config)
        app.state.yookassa = yookassa_client
        # Separate http client for /receipts polling — same auth as the main client.
        app.state.yookassa_receipts_http = httpx.AsyncClient(
            base_url=settings.yookassa_base_url,
            timeout=30.0,
            auth=(
                settings.yookassa_shop_id,
                settings.yookassa_secret_key.get_secret_value(),
            ),
        )
        log.info("yookassa_ready", shop_id=settings.yookassa_shop_id)
    else:
        log.info("yookassa_disabled_no_credentials")

    # --- Telegram bot (Sprint 4 Поток M) ---
    # Optional integration; only constructed when TELEGRAM_BOT_TOKEN is set.
    # The webhook receiver reads from ``app.state.telegram_bot`` — None
    # there means "bot not configured" → webhook quietly returns 200.
    tg_bot_token = settings.telegram_bot_token.get_secret_value()
    telegram_bot: TelegramBotClient | None = None
    if tg_bot_token:
        try:
            telegram_bot = TelegramBotClient(
                tg_bot_token,
                proxy=settings.outbound_http_proxy,
            )
            app.state.telegram_bot = telegram_bot
            log.info(
                "telegram_bot_ready",
                username=settings.telegram_bot_username,
                proxy_configured=bool(settings.outbound_http_proxy),
            )
        except ValueError as exc:
            log.warning("telegram_bot_init_failed", error=str(exc))
    else:
        app.state.telegram_bot = None
        log.info("telegram_bot_disabled_no_token")

    # --- «Мой налог» fallback ---
    lknpd_client: LknpdClient | None = None
    if settings.npd_enabled and settings.npd_inn and settings.npd_password.get_secret_value():
        lknpd_client = LknpdClient(
            LknpdConfig(
                inn=settings.npd_inn,
                password=settings.npd_password.get_secret_value(),
            )
        )
        app.state.lknpd_client = lknpd_client
        log.info("lknpd_ready")

    # --- Autorefill background task ---
    autorefill_task: asyncio.Task[None] | None = None
    autorefill_stop = asyncio.Event()
    if settings.autorefill_enabled and yookassa_client is not None:
        autorefill_task = asyncio.create_task(
            autorefill_loop(
                session_factory=get_session_factory(),
                yookassa=yookassa_client,
                redis=redis_client,
                interval_seconds=settings.autorefill_interval_seconds,
                stop_event=autorefill_stop,
            ),
            name="autorefill",
        )
        log.info("autorefill_started", interval=settings.autorefill_interval_seconds)

    # --- Hold janitor (TD-028) ---
    janitor_stop = asyncio.Event()
    janitor_task: asyncio.Task[None] | None = asyncio.create_task(
        hold_janitor_loop(
            session_factory=get_session_factory(),
            interval_seconds=settings.hold_janitor_interval_seconds,
            grace_seconds=settings.hold_janitor_grace_seconds,
            stop_event=janitor_stop,
        ),
        name="hold_janitor",
    )

    # --- Account closure cron (Sprint 7) ---
    closure_stop = asyncio.Event()
    closure_task: asyncio.Task[None] | None = asyncio.create_task(
        account_closure_loop(
            session_factory=get_session_factory(),
            redis=redis_client,
            interval_seconds=settings.account_closure_interval_seconds,
            stop_event=closure_stop,
        ),
        name="account_closure",
    )

    pii_purge_stop = asyncio.Event()
    pii_purge_task: asyncio.Task[None] | None = asyncio.create_task(
        account_pii_purge_loop(
            session_factory=get_session_factory(),
            interval_seconds=settings.account_pii_purge_interval_seconds,
            stop_event=pii_purge_stop,
        ),
        name="account_pii_purge",
    )

    # --- Provider balance monitor (Sprint 14) ---
    # Build adapter map once и сохраняем на app.state. API endpoint
    # /v1/account/admin/provider_balances/refresh переиспользует adapters
    # (важно для test-friendliness и для re-use HTTP-pool).
    balance_adapters = build_adapters(settings)
    app.state.balance_adapters = balance_adapters

    balance_refresh_stop = asyncio.Event()
    balance_refresh_task: asyncio.Task[None] | None = None
    if settings.provider_balance_refresh_interval_seconds > 0:
        balance_refresh_task = asyncio.create_task(
            balance_refresh_loop(
                session_factory=get_session_factory(),
                adapters=balance_adapters,
                settings=settings,
                interval_seconds=settings.provider_balance_refresh_interval_seconds,
                stop_event=balance_refresh_stop,
            ),
            name="balance_refresh",
        )
        log.info(
            "balance_refresh_started",
            interval=settings.provider_balance_refresh_interval_seconds,
        )

    # --- Data export cleanup cron (Sprint 7) ---
    from voltari_gateway.billing.data_export_cron import data_export_cleanup_loop

    data_export_stop = asyncio.Event()
    data_export_task: asyncio.Task[None] | None = asyncio.create_task(
        data_export_cleanup_loop(
            session_factory=get_session_factory(),
            export_dir=settings.data_export_dir,
            interval_seconds=settings.data_export_cleanup_interval_seconds,
            stop_event=data_export_stop,
        ),
        name="data_export_cleanup",
    )

    # --- MCP session manager lifecycle ---
    # The streamable-HTTP transport spins up an anyio task group for SSE
    # session bookkeeping; that task group MUST live inside the FastAPI
    # lifespan so cancellation propagates correctly on shutdown. We pull
    # the manager that ``create_app`` attached to ``app.state`` and enter
    # its ``run()`` context here.
    #
    # Important: ``StreamableHTTPSessionManager.run()`` can only be
    # called once per instance. Tests that rebuild the app between
    # cases get a fresh instance via ``create_app`` so the constraint
    # is automatically satisfied.
    mcp_session_manager = app.state.mcp_session_manager
    async with mcp_session_manager.run():
        log.info("mcp_session_manager_started")
        try:
            yield
        finally:
            log.info("shutdown_started")
            await _teardown_background_tasks(
                autorefill_task=autorefill_task,
                autorefill_stop=autorefill_stop,
                janitor_task=janitor_task,
                janitor_stop=janitor_stop,
                closure_task=closure_task,
                closure_stop=closure_stop,
                pii_purge_task=pii_purge_task,
                pii_purge_stop=pii_purge_stop,
                data_export_task=data_export_task,
                data_export_stop=data_export_stop,
                balance_refresh_task=balance_refresh_task,
                balance_refresh_stop=balance_refresh_stop,
            )
            await aclose_adapters(balance_adapters)
            registry_to_close = getattr(app.state, "provider_registry", None)
            if registry_to_close is not None:
                await registry_to_close.aclose()
                if hasattr(app.state, "openai_provider"):
                    app.state.openai_provider = None
            if yookassa_client is not None:
                await yookassa_client.aclose()
                recv_http = getattr(app.state, "yookassa_receipts_http", None)
                if recv_http is not None:
                    await recv_http.aclose()
            if lknpd_client is not None:
                await lknpd_client.aclose()
            if telegram_bot is not None:
                await telegram_bot.aclose()
            set_rate_limiter(None)
            set_mcp_rate_limiter(None)
            set_mcp_redis(None)
            if redis_client is not None:
                await redis_client.aclose()
            await dispose_engine()
            log.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.brand_name} Gateway",
        version=__version__,
        docs_url="/docs" if settings.app_debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_debug else None,
        lifespan=_lifespan,
    )

    # Starlette's add_exception_handler typing expects (Request, Exception) → Response,
    # но идиоматичная реализация в FastAPI принимает узкий тип исключения. Это
    # корректно по runtime-семантике (Starlette сама сужает тип), но строгая
    # сигнатура type: ignore для подавления false-positive.
    app.add_exception_handler(GatewayError, gateway_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # CORS — required for the SPA dashboard to talk to the management API
    # cross-origin while still allowing cookies (allow_credentials=True).
    # ``allow_origins`` must be an explicit list (not "*") whenever
    # credentials are involved, otherwise the browser rejects the response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        # X-CSRF-Token — double-submit CSRF (Sprint 2 Поток D, csrf_protocol.md).
        # ``X-Requested-With`` — legacy CSRF header (TD-036). Frontend всё ещё
        # шлёт его параллельно с X-CSRF-Token (см. apps/web/src/lib/api.ts:173)
        # для совместимости со старым middleware. Без его добавления в allow_headers
        # браузер режет preflight 400 — signup ломается из-за «Failed to fetch»
        # (см. инцидент 2026-05-01). Уберём из allow_headers одновременно с
        # удалением legacy-логики на фронте.
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Requested-With",
        ],
        max_age=600,
    )

    app.include_router(auth_router, prefix="/v1/auth")
    # 2FA + sessions carry their own /v1/auth/* prefixes.
    app.include_router(auth_2fa_router)
    app.include_router(sessions_router)
    app.include_router(account_router, prefix="/v1/account")
    # Account closure flow (Sprint 7) — its router declares the
    # /v1/account prefix internally.
    app.include_router(account_closure_router)
    # Data export flow (Sprint 7) — also declares /v1/account internally.
    app.include_router(data_export_router)
    # Routing preferences (Sprint 7) — declares /v1/account internally.
    app.include_router(routing_prefs_router)
    # Activity feed (Sprint 8 F6) — declares /v1/account internally.
    app.include_router(activity_router)
    # BrikkoLens traces (Phase 5 #4 — 2026-05-09). /v1/account/traces.
    app.include_router(traces_router)
    # BrikkoLens analytics (Sprint 2). /v1/account/analytics/summary.
    app.include_router(analytics_router)
    # Platform admin status (CEO single-pane). /v1/account/admin/status.
    app.include_router(admin_status_router)
    # Sprint S1 — Smart Router v2 per-account opt-in admin endpoint.
    app.include_router(admin_smart_router_v2_router)
    # Sprint 14 — admin balances (CEO sees upstream-account balances).
    # /v1/account/admin/provider_balances{,/refresh,/{provider}/manual}.
    app.include_router(admin_balances_router)
    # Sprint 14 Phase 2 — admin cookies (CEO uploads provider session cookies
    # for the Playwright scraper).  /v1/account/admin/provider_cookies.
    app.include_router(admin_cookies_router)
    # Tariff carries its own /v1/account/tariff prefix.
    app.include_router(tariff_router)
    # Seats / invites carry their own prefix (/v1/account/seats and
    # /v1/account/invites) so they're declared without a prefix= here.
    app.include_router(account_seats_router)
    app.include_router(account_invites_router)
    app.include_router(keys_router, prefix="/v1/keys")
    # Sprint MCP S1 — Brikko-MCP token CRUD (token-management surface only;
    # the MCP server itself lands in S2 under /mcp/* — separate ASGI mount).
    app.include_router(mcp_keys_router, prefix="/v1/mcp/tokens")
    app.include_router(chat_router)
    # Sprint 11.6 — native Anthropic Messages API for Claude Code SDK / Cursor.
    # Same /v1 prefix pattern as chat; the router itself declares /v1/messages.
    app.include_router(messages_router)
    # Sprint M1 — Speech-to-Text (Whisper) and embeddings. Routers declare
    # their own /v1/audio/transcriptions and /v1/embeddings paths, so no
    # prefix= here.
    app.include_router(audio_router)
    app.include_router(embeddings_router)
    # Sprint M2 — Image generation. Router declares /v1/images/generations
    # internally, no prefix= here.
    app.include_router(images_router)
    # M2 — standalone PII masking endpoints для SDK / Brikko Skill / n8n.
    # Router declares /anonymize и /restore — глобальный prefix /v1.
    app.include_router(anonymize_router, prefix="/v1")
    app.include_router(models_router)
    # Public sandbox (Sprint 12) — anonymous, no auth, rate-limited per-IP.
    # Lives under /v1/public/* to make middleware-stack disambiguation
    # trivial (chat is /v1/chat/*, sandbox is /v1/public/*).
    app.include_router(playground_router, prefix="/v1/public")
    # OAuth2 + PKCE (Pre-M0) — Studio onboarding. Router carries its own
    # /v1/oauth prefix; no prefix= here.
    app.include_router(oauth_router)
    # Social login (Google + Yandex). Carries its own /v1/auth/oauth
    # prefix; declared without prefix= here.
    app.include_router(oauth_login_router)
    # Public live-metrics widget (Sprint 12.6) — anonymous, cached 30 s,
    # rate-limited per-IP, never 5xx. Same /v1/public/* prefix family
    # as the sandbox so middleware-stack disambiguation stays trivial.
    app.include_router(public_status_router, prefix="/v1/public")
    app.include_router(usage_router)
    app.include_router(billing_router)
    app.include_router(health_router)
    # Telegram (Sprint 4 Поток M): /v1/account/telegram-link + webhook.
    app.include_router(telegram_link_router, prefix="/v1/account")
    app.include_router(telegram_webhook_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # --- Brikko-MCP endpoint (Sprint MCP S2) ---
    # Streamable-HTTP transport mounted under /mcp. The session manager
    # is built here (cheap, pure-Python) and stashed on app.state so the
    # lifespan can run its anyio task group. The ASGI wrapper handles
    # auth + rate-limit before delegating to the manager.
    mcp_session_manager = build_session_manager()
    app.state.mcp_session_manager = mcp_session_manager
    app.mount("/mcp", build_mcp_asgi(mcp_session_manager))

    # Prometheus /metrics + request middleware. Mounted last so all
    # other routers/middleware are visible to the instrumentation.
    setup_prometheus(app, settings)

    return app


app = create_app()
