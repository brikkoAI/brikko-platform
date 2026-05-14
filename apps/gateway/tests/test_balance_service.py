"""Service-layer tests for balance_monitor.

Coverage:
* native_to_rub_kopecks — USD/EUR/CNY/RUB/tokens conversions.
* compute_burn_and_runway — sums usage_events за окно, корректный divide.
* refresh_all — ok adapter + failing adapter не блокируют друг друга;
  rows записаны корректно (status, last_success_at).
* set_manual — записывает с fetch_method='manual' и ``manual`` status.
* get_all — возвращает 10 строк (KNOWN_PROVIDERS), pending для не-залитых.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.balance_monitor.service import (
    KNOWN_PROVIDERS,
    compute_burn_and_runway,
    get_all,
    native_to_rub_kopecks,
    refresh_all,
    set_manual,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ProviderBalance,
    Tariff,
    UsageEvent,
    User,
)

# ---------------------------------------------------------------------------
# Stub adapter (per-test)
# ---------------------------------------------------------------------------


class _StubAdapter(BalanceAdapter):
    """In-memory adapter — does not hit network. Used to exercise refresh_all."""

    is_remote = True

    def __init__(self, name: str, snapshot: BalanceSnapshot) -> None:
        self.provider_name = name
        self._snap = snapshot

    async def fetch(self) -> BalanceSnapshot:
        return self._snap


class _RaisingAdapter(BalanceAdapter):
    is_remote = True

    def __init__(self, name: str) -> None:
        self.provider_name = name

    async def fetch(self) -> BalanceSnapshot:
        raise RuntimeError("upstream-fail")


# ---------------------------------------------------------------------------
# native_to_rub_kopecks
# ---------------------------------------------------------------------------


def test_fx_conversion_usd() -> None:
    settings = get_settings()
    # USD=80₽ default, so 50 USD = 4000 ₽ = 400_000 kopecks
    out = native_to_rub_kopecks(
        balance_native=Decimal("50.00"),
        currency="USD",
        settings=settings,
    )
    assert out == 50 * settings.usd_to_rub * 100


def test_fx_conversion_eur() -> None:
    settings = get_settings()
    out = native_to_rub_kopecks(
        balance_native=Decimal("10"),
        currency="EUR",
        settings=settings,
    )
    assert out == 10 * settings.eur_to_rub * 100


def test_fx_conversion_rub_passthrough() -> None:
    settings = get_settings()
    # 100 RUB = 10_000 kopecks
    out = native_to_rub_kopecks(
        balance_native=Decimal("100.00"),
        currency="RUB",
        settings=settings,
    )
    assert out == 100 * 100


def test_fx_conversion_unknown_currency_returns_none() -> None:
    settings = get_settings()
    assert (
        native_to_rub_kopecks(
            balance_native=Decimal("1"),
            currency="GBP",
            settings=settings,
        )
        is None
    )


def test_fx_conversion_tokens_returns_none() -> None:
    """Sber tokens — нельзя пересчитать в рубли (см. adapters/sber.py)."""
    settings = get_settings()
    assert (
        native_to_rub_kopecks(
            balance_native=Decimal("1000000"),
            currency="tokens",
            settings=settings,
        )
        is None
    )


def test_fx_conversion_none_balance_returns_none() -> None:
    settings = get_settings()
    assert native_to_rub_kopecks(balance_native=None, currency="USD", settings=settings) is None


# ---------------------------------------------------------------------------
# compute_burn_and_runway
# ---------------------------------------------------------------------------


async def _seed_account(db) -> Account:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    acc = Account(
        owner_id=user.id,
        name="Acme",
        balance_kopecks=10_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=False,
    )
    db.add(acc)
    await db.flush()
    return acc


@pytest.mark.asyncio
async def test_burn_no_usage_returns_none(db) -> None:
    burn, runway = await compute_burn_and_runway(
        db,
        provider="deepseek",
        balance_rub_kopecks=100_000_00,
    )
    assert burn is None
    assert runway is None


@pytest.mark.asyncio
async def test_burn_and_runway_positive(db) -> None:
    acc = await _seed_account(db)
    now = datetime.now(UTC)
    # 7 days * 100₽ = 700₽ total → 100₽/day burn = 10_000 kopecks/day
    for i in range(7):
        db.add(
            UsageEvent(
                account_id=acc.id,
                api_key_id=None,
                model="gpt-5.4",
                provider="deepseek",
                modality="chat",
                unit="token",
                input_tokens=100,
                output_tokens=50,
                cached_tokens=0,
                cost_kopecks=10_000,
                request_id=f"req-{i}",
                created_at=now - timedelta(days=i, hours=1),
            )
        )
    await db.commit()

    burn, runway = await compute_burn_and_runway(
        db,
        provider="deepseek",
        balance_rub_kopecks=100_000,  # 1000 ₽
        now=now,
    )
    # 7 events * 10_000 kop ÷ 7 days = 10_000 / day
    assert burn == 10_000
    # 100_000 / 10_000 = 10 days
    assert runway == 10


@pytest.mark.asyncio
async def test_burn_outside_window_ignored(db) -> None:
    acc = await _seed_account(db)
    now = datetime.now(UTC)
    db.add(
        UsageEvent(
            account_id=acc.id,
            api_key_id=None,
            model="gpt-5.4",
            provider="deepseek",
            modality="chat",
            unit="token",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            cost_kopecks=10_000_000,
            request_id="old",
            # 30 days ago — outside 7-day window
            created_at=now - timedelta(days=30),
        )
    )
    await db.commit()
    burn, runway = await compute_burn_and_runway(
        db,
        provider="deepseek",
        balance_rub_kopecks=100_000_00,
        now=now,
    )
    assert burn is None
    assert runway is None


@pytest.mark.asyncio
async def test_burn_with_no_balance_returns_burn_only(db) -> None:
    """Burn рассчитывается, runway = None если balance не известен."""
    acc = await _seed_account(db)
    now = datetime.now(UTC)
    db.add(
        UsageEvent(
            account_id=acc.id,
            api_key_id=None,
            model="gpt-5.4",
            provider="deepseek",
            modality="chat",
            unit="token",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            cost_kopecks=70_000,
            request_id="r1",
            created_at=now - timedelta(hours=1),
        )
    )
    await db.commit()
    burn, runway = await compute_burn_and_runway(
        db,
        provider="deepseek",
        balance_rub_kopecks=None,
        now=now,
    )
    assert burn == 10_000  # 70_000 / 7
    assert runway is None


# ---------------------------------------------------------------------------
# refresh_all — partial failure tolerance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_all_one_failing_doesnt_block_others(session_factory, db) -> None:
    settings = get_settings()
    # Three adapters: one ok, one returning error-snapshot, one raising
    # mid-fetch (caught by refresh_one's wrapper).
    ok_snap = BalanceSnapshot(
        provider="deepseek",
        balance_native=Decimal("12.50"),
        currency="USD",
        fetch_method="api",
        raw={"balance_infos": [{"currency": "USD", "total_balance": "12.50"}]},
        error=None,
    )
    err_snap = BalanceSnapshot.error_for("moonshot", "auth_401: bad")

    adapters = {
        "deepseek": _StubAdapter("deepseek", ok_snap),
        "moonshot": _StubAdapter("moonshot", err_snap),
        "sber": _RaisingAdapter("sber"),
    }

    results = await refresh_all(
        session_factory=session_factory,
        adapters=adapters,
        settings=settings,
    )
    assert set(results.keys()) == {"deepseek", "moonshot", "sber"}
    assert results["deepseek"].error is None
    assert results["moonshot"].error is not None
    assert results["sber"].error is not None and "internal" in results["sber"].error

    # DB was upserted for the OK row.
    rows = (await db.execute(select(ProviderBalance))).scalars().all()
    by_name = {r.provider: r for r in rows}
    assert "deepseek" in by_name
    deepseek_row = by_name["deepseek"]
    assert deepseek_row.fetch_status == "ok"
    assert deepseek_row.balance_native == Decimal("12.50")
    assert deepseek_row.balance_currency == "USD"
    assert deepseek_row.fetch_method == "api"
    assert deepseek_row.last_success_at is not None
    # 12.5 USD * 80 ₽ * 100 kop = 100_000 kop
    assert deepseek_row.balance_rub_kopecks == 100_000

    assert "moonshot" in by_name
    assert by_name["moonshot"].fetch_status == "error"
    assert by_name["moonshot"].error_message and "auth" in by_name["moonshot"].error_message
    assert by_name["moonshot"].balance_native is None
    assert by_name["moonshot"].balance_rub_kopecks is None


@pytest.mark.asyncio
async def test_refresh_preserves_last_success_on_subsequent_error(session_factory, db) -> None:
    settings = get_settings()
    ok_snap = BalanceSnapshot(
        provider="deepseek",
        balance_native=Decimal("5"),
        currency="USD",
        fetch_method="api",
        raw={},
        error=None,
    )
    bad_snap = BalanceSnapshot.error_for("deepseek", "http_500")

    # First refresh OK
    await refresh_all(
        session_factory=session_factory,
        adapters={"deepseek": _StubAdapter("deepseek", ok_snap)},
        settings=settings,
    )
    rows = (await db.execute(select(ProviderBalance))).scalars().all()
    first_success = rows[0].last_success_at
    assert first_success is not None

    # Second refresh returns error — last_success_at must NOT regress to None.
    await refresh_all(
        session_factory=session_factory,
        adapters={"deepseek": _StubAdapter("deepseek", bad_snap)},
        settings=settings,
    )
    db.expire_all()
    rows = (await db.execute(select(ProviderBalance))).scalars().all()
    row = rows[0]
    assert row.fetch_status == "error"
    assert row.last_success_at == first_success  # preserved


# ---------------------------------------------------------------------------
# set_manual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_manual_basic(db) -> None:
    settings = get_settings()
    view = await set_manual(
        db,
        provider="openai",
        balance_native=Decimal("50.25"),
        currency="USD",
        notes="топап 10.05",
        settings=settings,
    )
    await db.commit()
    assert view.provider == "openai"
    assert view.fetch_method == "manual"
    assert view.fetch_status == "manual"
    assert view.balance_native == Decimal("50.25")
    assert view.balance_currency == "USD"
    # 50.25 * 80 * 100 = 402_000
    assert view.balance_rub_kopecks == 402_000
    assert view.notes == "топап 10.05"


@pytest.mark.asyncio
async def test_set_manual_overrides_previous(db) -> None:
    settings = get_settings()
    await set_manual(
        db,
        provider="openai",
        balance_native=Decimal("10"),
        currency="USD",
        notes=None,
        settings=settings,
    )
    await db.commit()
    view = await set_manual(
        db,
        provider="openai",
        balance_native=Decimal("99"),
        currency="USD",
        notes="updated",
        settings=settings,
    )
    await db.commit()
    assert view.balance_native == Decimal("99")
    assert view.notes == "updated"

    rows = (await db.execute(select(ProviderBalance))).scalars().all()
    assert len([r for r in rows if r.provider == "openai"]) == 1


@pytest.mark.asyncio
async def test_set_manual_rejects_unknown_provider(db) -> None:
    settings = get_settings()
    with pytest.raises(ValueError, match="unknown provider"):
        await set_manual(
            db,
            provider="palm",
            balance_native=Decimal("1"),
            currency="USD",
            notes=None,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_set_manual_rejects_negative_balance(db) -> None:
    settings = get_settings()
    with pytest.raises(ValueError, match=">= 0"):
        await set_manual(
            db,
            provider="openai",
            balance_native=Decimal("-1"),
            currency="USD",
            notes=None,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_set_manual_rejects_unknown_currency(db) -> None:
    settings = get_settings()
    with pytest.raises(ValueError, match="unsupported currency"):
        await set_manual(
            db,
            provider="openai",
            balance_native=Decimal("1"),
            currency="GBP",
            notes=None,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_set_manual_tokens_currency_no_rub_conversion(db) -> None:
    settings = get_settings()
    view = await set_manual(
        db,
        provider="sber",
        balance_native=Decimal("1000000"),
        currency="tokens",
        notes=None,
        settings=settings,
    )
    await db.commit()
    assert view.balance_currency == "tokens"
    assert view.balance_rub_kopecks is None


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_returns_all_known_providers_with_pending(db) -> None:
    views = await get_all(db)
    names = [v.provider for v in views]
    assert names == list(KNOWN_PROVIDERS)
    for v in views:
        assert v.fetch_status == "pending"
        assert v.balance_native is None
        assert v.last_fetched_at is None


@pytest.mark.asyncio
async def test_get_all_returns_persisted_rows_for_those_known(db) -> None:
    settings = get_settings()
    await set_manual(
        db,
        provider="openai",
        balance_native=Decimal("12"),
        currency="USD",
        notes=None,
        settings=settings,
    )
    await db.commit()
    views = await get_all(db)
    by_name = {v.provider: v for v in views}
    assert by_name["openai"].fetch_status == "manual"
    assert by_name["openai"].balance_native == Decimal("12")
    # А остальные всё ещё pending
    assert by_name["anthropic"].fetch_status == "pending"
    assert len(views) == len(KNOWN_PROVIDERS)
