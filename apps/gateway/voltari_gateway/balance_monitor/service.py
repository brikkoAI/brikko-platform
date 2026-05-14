"""Service layer for provider balance monitoring.

Single source of truth для:
* построения списка adapter'ов из ``Settings`` (какой ключ задан → API,
  какой нет → manual);
* записи ``BalanceSnapshot`` в БД (upsert на ``provider``);
* нормализации native value → ₽-копейки через FX-курсы из config;
* вычисления ``burn_rate_kopecks_per_day`` / ``runway_days`` из
  ``usage_events`` за последние 7 дней;
* публичных функций ``refresh_all()`` / ``set_manual()`` / ``get_all()`` —
  используются API-роутером и cron-таском.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.balance_monitor.adapters import (
    BalanceAdapter,
    BalanceSnapshot,
    DeepSeekBalanceAdapter,
    ManualAdapter,
    MiniMaxBalanceAdapter,
    MoonshotBalanceAdapter,
    SberBalanceAdapter,
    ScrapeRemoteAdapter,
    ZhipuBalanceAdapter,
)
from voltari_gateway.config import Settings
from voltari_gateway.db.models import ProviderBalance, UsageEvent
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# All provider keys we surface in the admin UI.  Order is the display
# order in the admin list (CEO requested grouping: paid foreign first,
# then RU, then placeholder-rest).
KNOWN_PROVIDERS: tuple[str, ...] = (
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "moonshot",
    "minimax",
    "zhipu",
    "together",
    "yandex",
    "sber",
)


# Which providers have a working API-adapter в фазе 1 (+ MiniMax/Zhipu —
# Sprint 14.2, 2026-05-12: китайские провайдеры с public balance API).
API_ADAPTER_PROVIDERS: frozenset[str] = frozenset(
    {"deepseek", "sber", "moonshot", "minimax", "zhipu"}
)

# Which providers gain a Playwright scrape-adapter when SCRAPER_URL is set
# (Phase 2, 2026-05-11).  Outside that flag they fall back to ManualAdapter.
SCRAPE_ADAPTER_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "together"})


@dataclass(frozen=True)
class ProviderBalanceView:
    """Read-shaped projection that the API endpoint returns.

    Detached from the ORM row so the API contract is stable across
    schema changes.
    """

    provider: str
    balance_native: Decimal | None
    balance_currency: str | None
    balance_rub_kopecks: int | None
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    fetch_status: str
    fetch_method: str
    error_message: str | None
    burn_rate_kopecks_per_day: int | None
    runway_days: int | None
    notes: str | None


# ---------------------------------------------------------------------------
# Adapter registry (built once per process, used by refresh loop + endpoint)
# ---------------------------------------------------------------------------


def build_adapters(settings: Settings) -> dict[str, BalanceAdapter]:
    """Return ``{provider: BalanceAdapter}`` map for this deployment.

    Only providers with credentials configured AND with an API-adapter
    available end up as real adapters. Everything else is a ``ManualAdapter``
    so ``service.get_all()`` always returns one row per provider in
    ``KNOWN_PROVIDERS`` even when the corresponding key isn't set.
    """
    adapters: dict[str, BalanceAdapter] = {}

    if settings.deepseek_api_key.get_secret_value():
        adapters["deepseek"] = DeepSeekBalanceAdapter(
            api_key=settings.deepseek_api_key.get_secret_value(),
            # DeepSeek balance — РФ-прямой как и chat (см. main.py).
            outbound_proxy=None,
        )
    if settings.sber_auth_key.get_secret_value():
        adapters["sber"] = SberBalanceAdapter(
            auth_key=settings.sber_auth_key.get_secret_value(),
            scope=settings.sber_scope,
        )
    if settings.moonshot_api_key.get_secret_value():
        adapters["moonshot"] = MoonshotBalanceAdapter(
            api_key=settings.moonshot_api_key.get_secret_value(),
            outbound_proxy=settings.outbound_http_proxy,
        )
    if settings.minimax_api_key.get_secret_value():
        adapters["minimax"] = MiniMaxBalanceAdapter(
            api_key=settings.minimax_api_key.get_secret_value(),
            outbound_proxy=settings.outbound_http_proxy,
        )
    if settings.zhipu_api_key.get_secret_value():
        # Zhipu key format is ``<id>.<secret>``. Constructor raises ValueError
        # if the secret half is missing — surfaces here so the boot fails
        # loudly rather than silently degrading to ManualAdapter.
        adapters["zhipu"] = ZhipuBalanceAdapter(
            api_key=settings.zhipu_api_key.get_secret_value(),
            outbound_proxy=settings.outbound_http_proxy,
        )

    # Phase 2 (2026-05-11) — Playwright scrape adapters for OpenAI / Anthropic /
    # Together when SCRAPER_URL + SCRAPER_INTERNAL_TOKEN are both set.  Without
    # both we fall back to ManualAdapter so dev/test environments don't need
    # the scraper container running.
    scraper_url = settings.scraper_url
    scraper_token = settings.scraper_internal_token.get_secret_value()
    if scraper_url and scraper_token:
        for provider in SCRAPE_ADAPTER_PROVIDERS:
            adapters[provider] = ScrapeRemoteAdapter(
                provider=provider,
                scraper_url=scraper_url,
                internal_token=scraper_token,
                timeout_seconds=settings.scraper_timeout_seconds,
            )

    # Manual placeholders для остальных. ManualAdapter.is_remote = False,
    # refresh_all() их игнорирует.
    for name in KNOWN_PROVIDERS:
        if name not in adapters:
            adapters[name] = ManualAdapter(name)

    return adapters


async def aclose_adapters(adapters: dict[str, BalanceAdapter]) -> None:
    """Release HTTP clients owned by API-adapter'ами."""
    for adapter in adapters.values():
        try:
            await adapter.aclose()
        except Exception:  # pragma: no cover — close-time failures are non-fatal
            log.warning("balance_adapter_close_failed", provider=adapter.provider_name)


# ---------------------------------------------------------------------------
# FX normalization
# ---------------------------------------------------------------------------


def native_to_rub_kopecks(
    *,
    balance_native: Decimal | None,
    currency: str | None,
    settings: Settings,
) -> int | None:
    """Convert native balance + currency → ₽-копейки.

    Returns None when:
    * balance_native is None (fetch failed / manual not yet entered);
    * currency is unknown (e.g. ``tokens`` for Sber — мы не пересчитываем
      токены в рубли, см. ``adapters/sber.py``).

    Hardcoded rates from ``config``: USD=80, EUR=88, CNY=11. ``RUB`` —
    pass-through (×100 — уже копейки).
    """
    if balance_native is None or currency is None:
        return None

    cur = currency.upper()
    rate_map: dict[str, int] = {
        "USD": settings.usd_to_rub,
        "EUR": settings.eur_to_rub,
        "CNY": settings.cny_to_rub,
    }
    if cur == "RUB":
        # RUB native — multiply by 100 to convert to копейки.
        return int((balance_native * Decimal(100)).quantize(Decimal("1")))
    rate = rate_map.get(cur)
    if rate is None:
        return None
    rub = balance_native * Decimal(rate)
    kopecks = (rub * Decimal(100)).quantize(Decimal("1"))
    return int(kopecks)


# ---------------------------------------------------------------------------
# Burn rate / runway
# ---------------------------------------------------------------------------


async def compute_burn_and_runway(
    db: AsyncSession,
    *,
    provider: str,
    balance_rub_kopecks: int | None,
    window_days: int = 7,
    now: datetime | None = None,
) -> tuple[int | None, int | None]:
    """Return ``(burn_rate_kopecks_per_day, runway_days)``.

    Burn rate = SUM(usage_events.cost_kopecks) for ``provider`` за последние
    ``window_days`` ÷ ``window_days``. None если нет usage за окно
    (новый провайдер / отключён).

    Runway = balance_rub_kopecks // burn_rate_kopecks_per_day. None if
    balance is None (manual not yet entered) или burn_rate=0.

    NB: cost_kopecks в ``usage_events`` это ₽-копейки (gateway уже пересчитал
    провайдерскую цену через fx_usd_rub в момент записи). См. chat.py:1074.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    stmt = select(func.coalesce(func.sum(UsageEvent.cost_kopecks), 0)).where(
        UsageEvent.provider == provider,
        UsageEvent.created_at >= cutoff,
    )
    total = int((await db.execute(stmt)).scalar_one() or 0)
    if total <= 0:
        return None, None

    burn_per_day = total // max(window_days, 1)
    if burn_per_day <= 0:
        return None, None

    if balance_rub_kopecks is None or balance_rub_kopecks <= 0:
        return burn_per_day, None
    runway = balance_rub_kopecks // burn_per_day
    return burn_per_day, int(runway)


# ---------------------------------------------------------------------------
# Persist (upsert)
# ---------------------------------------------------------------------------


async def _upsert_balance(
    db: AsyncSession,
    *,
    provider: str,
    values: dict[str, Any],
) -> None:
    """Upsert a row keyed on ``provider``.

    PG uses ``ON CONFLICT (provider) DO UPDATE``. SQLite (tests) uses the
    same idiom via the sqlite dialect's insert().on_conflict_do_update.
    """
    bind = db.get_bind()
    dialect_name = bind.dialect.name

    payload = dict(values)
    payload.setdefault("provider", provider)
    # ``updated_at`` always advances on upsert.
    payload["updated_at"] = datetime.now(UTC)

    if dialect_name == "postgresql":
        stmt = pg_insert(ProviderBalance).values(**payload)
        update_cols = {k: stmt.excluded[k] for k in payload if k != "provider"}
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider"],
            set_=update_cols,
        )
        await db.execute(stmt)
        return

    if dialect_name == "sqlite":
        stmt_lite = sqlite_insert(ProviderBalance).values(**payload)
        update_cols = {k: stmt_lite.excluded[k] for k in payload if k != "provider"}
        stmt_lite = stmt_lite.on_conflict_do_update(
            index_elements=["provider"],
            set_=update_cols,
        )
        await db.execute(stmt_lite)
        return

    # Generic fallback (no dialect-specific upsert) — try insert, fallback
    # to update. Not used in production; here so future test backends don't
    # silently break.
    existing = await db.execute(select(ProviderBalance).where(ProviderBalance.provider == provider))
    row = existing.scalar_one_or_none()
    if row is None:
        db.add(ProviderBalance(**payload))
    else:
        for k, v in payload.items():
            if k == "provider":
                continue
            setattr(row, k, v)


# ---------------------------------------------------------------------------
# Public API: refresh_all / set_manual / get_all
# ---------------------------------------------------------------------------


async def refresh_one(
    db: AsyncSession,
    adapter: BalanceAdapter,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> BalanceSnapshot:
    """Fetch one adapter, persist, return the snapshot.

    Errors are recorded as ``fetch_status='error'`` with ``error_message``.
    The snapshot itself is always returned (never raises) so the caller
    can show per-provider results.
    """
    now = now or datetime.now(UTC)
    if not adapter.is_remote:
        # Manual adapter — refresh должен быть NO-OP. Возвращаем пустой
        # snapshot чтобы UI отрисовал «n/a» вместо ошибки.
        return BalanceSnapshot(
            provider=adapter.provider_name,
            balance_native=None,
            currency=None,
            fetch_method="manual",
            raw=None,
            error=None,
        )

    snapshot = await adapter.fetch()

    rub_kopecks = native_to_rub_kopecks(
        balance_native=snapshot.balance_native,
        currency=snapshot.currency,
        settings=settings,
    )

    burn_per_day, runway = await compute_burn_and_runway(
        db,
        provider=adapter.provider_name,
        balance_rub_kopecks=rub_kopecks,
        now=now,
    )

    if snapshot.error is None and snapshot.balance_native is not None:
        fetch_status = "ok"
        last_success_at: datetime | None = now
    else:
        fetch_status = "error"
        last_success_at = None  # don't bump; preserved by upsert COALESCE below

    # If error and we already had a last_success_at, keep it. We can't
    # COALESCE in upsert with ON CONFLICT generically — read existing
    # value and pass it through.
    if fetch_status == "error":
        existing = await db.execute(
            select(ProviderBalance.last_success_at).where(
                ProviderBalance.provider == adapter.provider_name
            )
        )
        prior = existing.scalar_one_or_none()
        last_success_at = prior

    # Honour the snapshot's own fetch_method — scrape-adapter sets ``"scrape"``,
    # API-adapters set ``"api"``. Mismatch with the DB CHECK constraint would
    # blow up the upsert; ``BalanceSnapshot`` is responsible for sending one
    # of {api, scrape}.
    fetch_method = snapshot.fetch_method or "api"

    values: dict[str, Any] = {
        "balance_native": snapshot.balance_native,
        "balance_currency": snapshot.currency,
        "balance_rub_kopecks": rub_kopecks if fetch_status == "ok" else None,
        "last_fetched_at": now,
        "last_success_at": last_success_at,
        "fetch_status": fetch_status,
        "fetch_method": fetch_method,
        "error_message": snapshot.error,
        "raw_response": snapshot.raw,
        "burn_rate_kopecks_per_day": burn_per_day,
        "runway_days": runway,
    }

    await _upsert_balance(db, provider=adapter.provider_name, values=values)
    return snapshot


async def refresh_all(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    adapters: dict[str, BalanceAdapter],
    settings: Settings,
) -> dict[str, BalanceSnapshot]:
    """Fan out fetch() across all API-adapters in parallel.

    Each adapter runs in its own DB session — one slow / failing adapter
    doesn't block the others, and a DB error inside one adapter doesn't
    poison the rest of the batch.
    """
    api_adapters = [a for a in adapters.values() if a.is_remote]

    async def _one(adapter: BalanceAdapter) -> BalanceSnapshot:
        async with session_factory() as db:
            try:
                snapshot = await refresh_one(db, adapter, settings=settings)
                await db.commit()
                return snapshot
            except Exception as exc:
                await db.rollback()
                log.exception(
                    "balance_refresh_one_failed",
                    provider=adapter.provider_name,
                )
                return BalanceSnapshot.error_for(adapter.provider_name, f"internal: {exc}")

    results = await asyncio.gather(
        *(_one(a) for a in api_adapters),
        return_exceptions=False,
    )
    return {snap.provider: snap for snap in results}


async def set_manual(
    db: AsyncSession,
    *,
    provider: str,
    balance_native: Decimal,
    currency: str,
    notes: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> ProviderBalanceView:
    """Record a manually-entered balance.

    ``provider`` MUST be one of ``KNOWN_PROVIDERS``.
    ``balance_native`` MUST be >= 0 (we treat -ve as input error,
    not a credit).
    Currency must be in our FX map (USD/EUR/CNY/RUB) или ``"tokens"``.
    """
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown provider: {provider!r}")
    if balance_native < 0:
        raise ValueError("balance_native must be >= 0")
    cur_norm = currency.upper().strip()
    if cur_norm not in {"USD", "EUR", "CNY", "RUB", "TOKENS"}:
        raise ValueError(f"unsupported currency: {currency!r}")

    now = now or datetime.now(UTC)

    # FX nominalisation (None for tokens).
    if cur_norm == "TOKENS":
        rub_kopecks: int | None = None
    else:
        rub_kopecks = native_to_rub_kopecks(
            balance_native=balance_native,
            currency=cur_norm,
            settings=settings,
        )

    burn_per_day, runway = await compute_burn_and_runway(
        db,
        provider=provider,
        balance_rub_kopecks=rub_kopecks,
        now=now,
    )

    values: dict[str, Any] = {
        "balance_native": balance_native,
        "balance_currency": cur_norm if cur_norm != "TOKENS" else "tokens",
        "balance_rub_kopecks": rub_kopecks,
        "last_fetched_at": now,
        "last_success_at": now,
        "fetch_status": "manual",
        "fetch_method": "manual",
        "error_message": None,
        "raw_response": None,
        "burn_rate_kopecks_per_day": burn_per_day,
        "runway_days": runway,
        "notes": notes,
    }

    await _upsert_balance(db, provider=provider, values=values)
    await db.flush()

    row = (
        await db.execute(select(ProviderBalance).where(ProviderBalance.provider == provider))
    ).scalar_one()
    return _row_to_view(row)


def _row_to_view(row: ProviderBalance) -> ProviderBalanceView:
    return ProviderBalanceView(
        provider=row.provider,
        balance_native=row.balance_native,
        balance_currency=row.balance_currency,
        balance_rub_kopecks=row.balance_rub_kopecks,
        last_fetched_at=row.last_fetched_at,
        last_success_at=row.last_success_at,
        fetch_status=row.fetch_status,
        fetch_method=row.fetch_method,
        error_message=row.error_message,
        burn_rate_kopecks_per_day=row.burn_rate_kopecks_per_day,
        runway_days=row.runway_days,
        notes=row.notes,
    )


async def get_all(db: AsyncSession) -> list[ProviderBalanceView]:
    """Return one ``ProviderBalanceView`` per ``KNOWN_PROVIDERS``.

    Providers with no row yet are returned as a synthetic view with
    ``fetch_status='pending'`` and all balance-fields ``None`` so the
    admin UI shows the full provider list from day one.
    """
    rows = (await db.execute(select(ProviderBalance))).scalars().all()
    by_name = {row.provider: row for row in rows}
    out: list[ProviderBalanceView] = []
    for name in KNOWN_PROVIDERS:
        row = by_name.get(name)
        if row is not None:
            out.append(_row_to_view(row))
        else:
            # Inferred default ``fetch_method`` for the synthetic "pending" row:
            #   API_ADAPTER_PROVIDERS  → "api"
            #   SCRAPE_ADAPTER_PROVIDERS → "scrape"
            #   anything else            → "manual"
            # NB: this is just the **display hint** for the admin UI when no
            # real row exists yet; the actual method comes from the DB once
            # refresh_all() has run.
            if name in API_ADAPTER_PROVIDERS:
                default_method = "api"
            elif name in SCRAPE_ADAPTER_PROVIDERS:
                default_method = "scrape"
            else:
                default_method = "manual"
            out.append(
                ProviderBalanceView(
                    provider=name,
                    balance_native=None,
                    balance_currency=None,
                    balance_rub_kopecks=None,
                    last_fetched_at=None,
                    last_success_at=None,
                    fetch_status="pending",
                    fetch_method=default_method,
                    error_message=None,
                    burn_rate_kopecks_per_day=None,
                    runway_days=None,
                    notes=None,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Cron loop (registered from main.lifespan)
# ---------------------------------------------------------------------------


async def balance_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    adapters: dict[str, BalanceAdapter],
    settings: Settings,
    interval_seconds: int = 21_600,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running task that fires ``refresh_all`` every ``interval_seconds``.

    Same shape as ``billing.autorefill.autorefill_loop`` — single-process
    soloo-stage. No Redis lock needed: API balance endpoints are read-only
    and idempotent, parallel refreshes from two replicas are harmless.

    A clean shutdown is achieved by setting ``stop_event``.
    """
    log.info(
        "balance_refresh_loop_started",
        interval=interval_seconds,
        api_providers=sorted([n for n, a in adapters.items() if a.is_remote]),
    )
    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("balance_refresh_loop_stopped")
            return
        try:
            results = await refresh_all(
                session_factory=session_factory,
                adapters=adapters,
                settings=settings,
            )
            ok_count = sum(1 for s in results.values() if s.error is None)
            err_count = len(results) - ok_count
            log.info(
                "balance_refresh_tick_done",
                ok=ok_count,
                errors=err_count,
            )
        except Exception:
            log.exception("balance_refresh_tick_error")

        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return  # stop_event signalled
            await asyncio.sleep(interval_seconds)
        except TimeoutError:
            continue


__all__ = [
    "API_ADAPTER_PROVIDERS",
    "KNOWN_PROVIDERS",
    "SCRAPE_ADAPTER_PROVIDERS",
    "ProviderBalanceView",
    "aclose_adapters",
    "balance_refresh_loop",
    "build_adapters",
    "compute_burn_and_runway",
    "get_all",
    "native_to_rub_kopecks",
    "refresh_all",
    "refresh_one",
    "set_manual",
]
