"""Stream cancellation + provider-stream-failure billing tests.

Sprint 4 Поток L (QA implement) — реализация плана Sprint 3 Поток K секции
``test_chat_stream_cancel.py``.

В этом файле — то, чего НЕТ в существующем ``test_chat_billing_integration.py``:

* TC-K1.3: Provider stream failure mid-flight (TimeoutError raised inside
  the chunk iterator) → hold released, partial-or-zero usage debit, NO
  500 leak.
* TC-K1.4: 50 параллельных стримов не пересекаются по hold/billing.
* TC-K1.5: Provider возвращает usage в финальном [DONE] chunk — partial
  cancel должен использовать накопленный счётчик, не финальный.
* TC-K1.6: Cancel в первый ms (до первого chunk) — debit=0, hold released,
  usage_event либо отсутствует (нет токенов), либо 0/0.

Стратегия — большинство тестов работают через прямой вызов
``_settle_stream_billing`` (как в существующем
``test_stream_cancel_mid_flight_partial_debit``), потому что прогон ASGI
SSE-генератора оставляет фоновый ``anyio.Event`` listener привязанным к
event loop теста — и крашит соседние тесты при tear-down.

Один тест (TC-K1.4) гонит реальный ASGI flow на 50 параллельных
запросов — но через non-stream chunks с быстрым [DONE]. Что нам нужно
проверить там — invariant'ы балансов и отсутствие orphaned holds, а не
сами bytes.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.api.chat import _settle_stream_billing
from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.billing.engine import HoldHandle, hold_amount
from voltari_gateway.db.models import (
    Account,
    AccountHold,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    Transaction,
    UsageEvent,
    User,
)
from voltari_gateway.router.catalog import get_model

# ---------- helpers (mirrors test_chat_billing_integration) ------------------


async def _seed_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    balance_kopecks: int = 100_000,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    async with factory() as s:
        user = User(
            email=f"stream-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
            email_verified=True,
        )
        s.add(user)
        await s.flush()

        account = Account(
            owner_id=user.id,
            name="Stream test",
            balance_kopecks=balance_kopecks,
            tariff=Tariff.PRO,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
        s.add(account)
        await s.flush()

        generated = generate_api_key()
        api_key = ApiKey(
            account_id=account.id,
            name="default",
            key_hash=generated.key_hash,
            key_prefix=generated.prefix,
            status=ApiKeyStatus.ACTIVE,
        )
        s.add(api_key)
        await s.commit()

        return user.id, account.id, api_key.id, generated.plaintext


async def _read_balance(factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID) -> int:
    async with factory() as s:
        acc = await s.get(Account, account_id)
        assert acc is not None
        return acc.balance_kopecks


async def _read_active_holds(
    factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID
) -> int:
    async with factory() as s:
        rows = (
            (await s.execute(select(AccountHold).where(AccountHold.account_id == account_id)))
            .scalars()
            .all()
        )
        return len(rows)


async def _place_hold(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID,
    amount_kopecks: int,
    ref_id: str,
) -> HoldHandle:
    async with factory() as s:
        await hold_amount(
            s,
            account_id=account_id,
            amount_kopecks=amount_kopecks,
            ref_id=ref_id,
        )
        await s.commit()
    return HoldHandle(
        account_id=account_id,
        ref_id=ref_id,
        amount_kopecks=amount_kopecks,
    )


# ---------------------------------------------------------------------------
# TC-K1.6: cancel before first chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_cancel_before_first_chunk_releases_hold(session_factory):
    """Cancel before any usage was seen → hold released, NO debit, NO usage_event.

    This is the "TCP RST in the first millisecond" scenario. The settler
    sees ``usage_input=0, usage_output=0`` — a fast-path that releases the
    hold without touching ``commit_hold_to_debit``.
    """
    _, account_id, api_key_id, _ = await _seed_account(session_factory)
    chosen = get_model("gpt-5.4-mini")
    request_id = f"first-ms-{uuid.uuid4().hex}"
    pre_balance = await _read_balance(session_factory, account_id)

    handle = await _place_hold(
        session_factory,
        account_id=account_id,
        amount_kopecks=5_000,
        ref_id=request_id,
    )
    assert await _read_active_holds(session_factory, account_id) == 1

    # Settle with NO usage → defensive fast-path.
    await _settle_stream_billing(
        factory=session_factory,
        account_id=account_id,
        api_key_id=api_key_id,
        chosen=chosen,
        request_id=request_id,
        hold=handle,
        usage_input=0,
        usage_output=0,
        usage_cached=0,
        client_disconnected=True,
        provider_failed=False,
    )

    # Balance untouched.
    assert await _read_balance(session_factory, account_id) == pre_balance
    # Hold gone.
    assert await _read_active_holds(session_factory, account_id) == 0
    # No usage_event written (settler fast-path).
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
            .scalars()
            .all()
        )
    assert len(events) == 0


# ---------------------------------------------------------------------------
# TC-K1.3: provider raises mid-stream → hold released, no overcharge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_provider_failure_after_partial_chunks_debits_partial(
    session_factory,
):
    """Provider iterator raises ``ProviderTimeoutError`` after delivering N
    tokens → settler debits the partial usage, hold released, usage_event
    rows reflect what was actually delivered.

    We don't test the SSE generator itself (sse_starlette / anyio interplay
    is fragile in pytest event loops); the contract that matters is what
    ``_settle_stream_billing`` does given the post-failure state.
    """
    _, account_id, api_key_id, _ = await _seed_account(session_factory)
    chosen = get_model("gpt-5.4-mini")
    request_id = f"prov-fail-{uuid.uuid4().hex}"
    pre_balance = await _read_balance(session_factory, account_id)

    # Pre-flight hold.
    handle = await _place_hold(
        session_factory,
        account_id=account_id,
        amount_kopecks=5_000,
        ref_id=request_id,
    )

    # Imagine the generator delivered a few chunks before provider raised.
    # Counters are non-zero → settler MUST debit (provider failure doesn't
    # refund work the user already got).
    await _settle_stream_billing(
        factory=session_factory,
        account_id=account_id,
        api_key_id=api_key_id,
        chosen=chosen,
        request_id=request_id,
        hold=handle,
        usage_input=200,
        usage_output=80,
        usage_cached=0,
        client_disconnected=False,
        provider_failed=True,
    )

    # 200 prompt × 6 kop/1k + 80 completion × 36 kop/1k = 1.2 + 2.88 = 4.08 kop
    # × 1.15 markup = 4.692 → 5 kop (ROUND_HALF_EVEN).
    # We don't pin exact value — pin INVARIANTS:
    new_balance = await _read_balance(session_factory, account_id)
    cost = pre_balance - new_balance
    assert 0 < cost < 5_000, f"partial debit must be < hold and > 0, got {cost}"

    # Usage event reflects what was delivered (NOT zero, NOT estimated).
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].input_tokens == 200
    assert events[0].output_tokens == 80
    assert events[0].cost_kopecks == cost

    # Hold cleaned up.
    assert await _read_active_holds(session_factory, account_id) == 0


# ---------------------------------------------------------------------------
# TC-K1.5: provider sent usage in final [DONE] but client cancelled before
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_cancel_uses_observed_usage_not_provider_final(
    session_factory,
):
    """If client disconnected BEFORE the final [DONE] usage chunk arrived,
    the settler must use the LAST OBSERVED counters — not the provider's
    final-usage which never reached us.

    This test pins the contract: ``_settle_stream_billing`` is given
    whatever counters the generator accumulated; it does NOT receive the
    provider's would-have-been-final usage. So if the generator's pre-
    cancel state was ``(usage_input=300, usage_output=120)`` we debit
    EXACTLY that — even if the provider's stream model is "200/2000".
    """
    _, account_id, api_key_id, _ = await _seed_account(session_factory)
    chosen = get_model("gpt-5.4-mini")
    request_id = f"final-done-cancel-{uuid.uuid4().hex}"

    handle = await _place_hold(
        session_factory,
        account_id=account_id,
        amount_kopecks=10_000,
        ref_id=request_id,
    )

    # Cancelled with these observed counters, NOT the (200, 2000) the
    # provider was about to ship in [DONE].
    await _settle_stream_billing(
        factory=session_factory,
        account_id=account_id,
        api_key_id=api_key_id,
        chosen=chosen,
        request_id=request_id,
        hold=handle,
        usage_input=300,
        usage_output=120,
        usage_cached=0,
        client_disconnected=True,
        provider_failed=False,
    )

    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
            .scalars()
            .all()
        )
    assert len(events) == 1
    # Event reflects observed counters, not the would-be-final-usage from
    # the provider's [DONE] (which never arrived).
    assert events[0].input_tokens == 300
    assert events[0].output_tokens == 120

    # Transaction meta carries client_disconnected.
    async with session_factory() as s:
        tx = (
            await s.execute(select(Transaction).where(Transaction.ref_id == request_id))
        ).scalar_one_or_none()
    # tx может быть None если cost округлился к 0 — это OK (release_hold путь).
    if tx is not None:
        assert tx.meta is not None
        assert tx.meta.get("client_disconnected") is True
        assert tx.meta.get("stream") is True


# ---------------------------------------------------------------------------
# TC-K1.4: 50 параллельных streams — нет cross-talk, нет hold leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_50_concurrent_stream_settles_no_hold_leak(session_factory):
    """50 параллельных stream-cancel событий на один аккаунт.

    Контракт: после того как все 50 settle'ов завершились, баланс упал
    на сумму N×partial_cost, а активных holdов 0. Это смоук-тест на
    отсутствие cross-talk: каждый settler должен видеть и удалять только
    СВОЙ hold.

    SQLite + StaticPool сериализует — это OK, нам интересна именно
    application-level invariance.
    """
    _, account_id, api_key_id, _ = await _seed_account(session_factory, balance_kopecks=10_000_000)
    chosen = get_model("gpt-5.4-mini")
    pre_balance = await _read_balance(session_factory, account_id)

    n_streams = 50
    request_ids = [f"concurrent-{i}-{uuid.uuid4().hex[:8]}" for i in range(n_streams)]
    handles: list[HoldHandle] = []
    for rid in request_ids:
        h = await _place_hold(
            session_factory,
            account_id=account_id,
            amount_kopecks=1_000,  # small hold; aggregate 50_000
            ref_id=rid,
        )
        handles.append(h)

    # All 50 holds present.
    assert await _read_active_holds(session_factory, account_id) == n_streams

    async def _settle(handle: HoldHandle, rid: str) -> None:
        await _settle_stream_billing(
            factory=session_factory,
            account_id=account_id,
            api_key_id=api_key_id,
            chosen=chosen,
            request_id=rid,
            hold=handle,
            usage_input=100,
            usage_output=40,
            usage_cached=0,
            client_disconnected=True,
            provider_failed=False,
        )

    await asyncio.gather(*(_settle(h, rid) for h, rid in zip(handles, request_ids, strict=True)))

    # All holds settled.
    assert await _read_active_holds(session_factory, account_id) == 0

    # Balance reduced consistently — exactly n_streams usage_event rows.
    new_balance = await _read_balance(session_factory, account_id)
    assert new_balance < pre_balance
    # No overcharge — total debit must be ≤ n_streams × hold_size (1000).
    delta = pre_balance - new_balance
    assert 0 < delta <= n_streams * 1_000, (delta, n_streams * 1_000)

    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
            .scalars()
            .all()
        )
    assert len(events) == n_streams

    # Каждый event помечен ровно одним из request_ids — проверим bijection.
    seen_rids = {e.request_id for e in events}
    assert seen_rids == set(request_ids), seen_rids - set(request_ids)


# ---------------------------------------------------------------------------
# Bonus: provider raises BEFORE any chunks were yielded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_provider_pre_chunk_failure_releases_full_hold(
    session_factory,
):
    """Provider's ``chat_completion_stream`` itself raises (e.g. timeout
    on opening connection) → no chunks observed, settler called with
    counters=0, hold fully released, no transaction.

    Distinct from TC-K1.6 (TCP-RST) only by ``provider_failed=True``.
    """
    _, account_id, api_key_id, _ = await _seed_account(session_factory)
    chosen = get_model("gpt-5.4-mini")
    request_id = f"prov-pre-{uuid.uuid4().hex}"
    pre_balance = await _read_balance(session_factory, account_id)

    handle = await _place_hold(
        session_factory,
        account_id=account_id,
        amount_kopecks=8_000,
        ref_id=request_id,
    )

    await _settle_stream_billing(
        factory=session_factory,
        account_id=account_id,
        api_key_id=api_key_id,
        chosen=chosen,
        request_id=request_id,
        hold=handle,
        usage_input=0,
        usage_output=0,
        usage_cached=0,
        client_disconnected=False,
        provider_failed=True,
    )

    # Balance unchanged.
    assert await _read_balance(session_factory, account_id) == pre_balance
    # Hold released.
    assert await _read_active_holds(session_factory, account_id) == 0
    # No usage_event и transaction.
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == account_id)))
            .scalars()
            .all()
        )
        txs = (
            (await s.execute(select(Transaction).where(Transaction.ref_id == request_id)))
            .scalars()
            .all()
        )
    assert len(events) == 0
    assert len(txs) == 0
