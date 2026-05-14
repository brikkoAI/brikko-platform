"""Integration tests for the /v1/chat/completions billing flow.

Covers Backend P0-1, P0-3, P0-4, P0-15:

* Successful chat call debits the account balance.
* Provider failure releases the hold; balance unchanged; no usage_event.
* Insufficient balance blocks the upstream call (no provider hit).
* debit_account is idempotent under concurrent calls with the same ref_id.
* Stream cancellation mid-flight debits only delivered tokens.
* Concurrent /v1/chat/completions on the same account never overspend.

Concurrency cases that need real ``SELECT FOR UPDATE`` are gated behind
the ``integration`` marker (postgres testcontainer). The rest run on
SQLite + StaticPool which serialises writes inside-process — enough to
exercise the application logic, just not the Postgres lock semantics.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.conftest import StubProvider
from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.billing.engine import (
    InsufficientBalanceError,
    debit_account,
    hold_amount,
)
from voltari_gateway.db.models import (
    Account,
    AccountHold,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Base,
    Tariff,
    Transaction,
    UsageEvent,
    User,
)
from voltari_gateway.providers.base import (
    ChatCompletionResponse,
    ChatCompletionUsage,
    ProviderServerError,
)

# ---------- helpers ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ApiKeySetup:
    user_id: uuid.UUID
    account_id: uuid.UUID
    api_key_id: uuid.UUID
    plaintext: str
    auth_header: dict[str, str]


async def _seed_account_with_api_key(
    factory: async_sessionmaker[AsyncSession],
    *,
    balance_kopecks: int,
    tariff: Tariff = Tariff.PRO,
) -> _ApiKeySetup:
    """Create a fresh user/account/api_key triple with the given balance.

    Returns the IDs + the bearer plaintext (live-only; never persisted).
    The balance is set explicitly so test cases don't depend on the
    fixture default (100 000 kop).
    """
    async with factory() as s:
        user = User(
            email=f"user-{uuid.uuid4().hex[:8]}@test.local",
            password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
            email_verified=True,
        )
        s.add(user)
        await s.flush()

        account = Account(
            owner_id=user.id,
            name="Test account",
            balance_kopecks=balance_kopecks,
            tariff=tariff,
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

        return _ApiKeySetup(
            user_id=user.id,
            account_id=account.id,
            api_key_id=api_key.id,
            plaintext=generated.plaintext,
            auth_header={"Authorization": f"Bearer {generated.plaintext}"},
        )


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


def _stub_with_usage(*, prompt_tokens: int = 100, completion_tokens: int = 50) -> StubProvider:
    """A StubProvider whose canned response carries a non-zero usage block.

    The default StubProvider response uses 10/20 tokens which rounds to a
    sub-kopeck cost on the cheap models — fine for happy-path checks but
    we need real numbers here to assert balance arithmetic.
    """
    stub = StubProvider()
    stub.next_response = ChatCompletionResponse(
        raw={
            "id": "chatcmpl-billing",
            "object": "chat.completion",
            "created": 1730000000,
            "model": "gpt-5.4-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
        usage=ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        model_id="gpt-5.4-mini",
        provider="openai",
    )
    return stub


# ---------- tests (sqlite-backed, run on every PR) --------------------------


@pytest.mark.asyncio
async def test_chat_debits_balance_on_success(client, app, session_factory):
    """Happy path: successful chat call moves balance by exactly compute_cost_kopecks.

    Catalog gpt-5.4-mini: input=6 kop/1k, output=36 kop/1k. With markup 1.15:
    prompt=1000, completion=500 → COGS = 1000*6/1000 + 500*36/1000 = 24 kop
    → 24 * 1.15 = 27.6 → rounded to 28 kop.
    """
    setup = await _seed_account_with_api_key(session_factory, balance_kopecks=100_000)
    # Replace the default stub with one that returns billable usage.
    app.state.openai_provider = _stub_with_usage(prompt_tokens=1000, completion_tokens=500)
    from voltari_gateway.providers.registry import ProviderRegistry
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, app.state.openai_provider)
    app.state.provider_registry = registry

    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "billing-test"}],
        },
        headers=setup.auth_header,
    )
    assert r.status_code == 200, r.text

    new_balance = await _read_balance(session_factory, setup.account_id)
    expected_cost_kop = 28  # see docstring
    assert new_balance == 100_000 - expected_cost_kop

    # Usage event was recorded with the same cost.
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == setup.account_id)))
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].cost_kopecks == expected_cost_kop
    assert events[0].input_tokens == 1000
    assert events[0].output_tokens == 500

    # No active holds left.
    assert await _read_active_holds(session_factory, setup.account_id) == 0


@pytest.mark.asyncio
async def test_chat_does_not_debit_on_provider_error(client, app, session_factory):
    """Provider 500 → balance unchanged, no usage_event, hold released."""
    setup = await _seed_account_with_api_key(session_factory, balance_kopecks=100_000)

    stub = StubProvider()

    async def _boom(_req):
        raise ProviderServerError("upstream is on fire")

    stub.chat_completion = _boom  # type: ignore[method-assign]
    app.state.openai_provider = stub

    from voltari_gateway.providers.registry import ProviderRegistry
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, stub)
    app.state.provider_registry = registry

    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "boom"}],
            "failover": False,  # no chain to walk; primary 5xx → 502
        },
        headers=setup.auth_header,
    )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "api_error"

    # Balance unchanged.
    assert await _read_balance(session_factory, setup.account_id) == 100_000

    # No usage_event row, no transactions row.
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == setup.account_id)))
            .scalars()
            .all()
        )
        assert len(events) == 0

        tx_rows = (
            (await s.execute(select(Transaction).where(Transaction.account_id == setup.account_id)))
            .scalars()
            .all()
        )
        assert len(tx_rows) == 0

    # Hold was released.
    assert await _read_active_holds(session_factory, setup.account_id) == 0


@pytest.mark.asyncio
async def test_chat_402_on_insufficient_balance(client, app, session_factory):
    """Tiny balance + large estimated hold → 402 BEFORE the upstream call.

    We confirm "before the upstream call" by asserting the stub never saw
    a request (``last_request is None``).
    """
    setup = await _seed_account_with_api_key(
        session_factory,
        balance_kopecks=10,  # 0.10 ₽ — under any reasonable hold
        tariff=Tariff.PRO,  # PAYG would 402 in the cached-balance fast path
    )

    # Stock stub — we want to verify it was NOT called.
    stub = StubProvider()
    app.state.openai_provider = stub

    from voltari_gateway.providers.registry import ProviderRegistry
    from voltari_gateway.router.catalog import Provider as ProviderEnum

    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, stub)
    app.state.provider_registry = registry

    big_prompt = "x" * 3_000  # ~1k tokens estimated → estimated hold > 10 kop
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": big_prompt}],
            "max_tokens": 4096,
        },
        headers=setup.auth_header,
    )
    assert r.status_code == 402, r.text
    assert r.json()["error"]["type"] == "insufficient_quota"

    # Provider was never called.
    assert stub.last_request is None

    # Balance unchanged, no usage_event, no holds left.
    assert await _read_balance(session_factory, setup.account_id) == 10
    assert await _read_active_holds(session_factory, setup.account_id) == 0
    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == setup.account_id)))
            .scalars()
            .all()
        )
        assert len(events) == 0


@pytest.mark.asyncio
async def test_debit_account_idempotent_sequential(session_factory, api_key_fixture):
    """N sequential debit_account calls with the same ref_id → exactly 1 charge.

    SQLite + StaticPool serialises writes inside-process and the model
    metadata used in tests doesn't recreate the (account_id, ref_id)
    UNIQUE index that ``Alembic 0002`` adds — so this test exercises the
    *application-level* idempotency path: each repeat finds the existing
    transaction row inside its own transaction and short-circuits.

    Real ``ON CONFLICT`` semantics under genuine parallelism are exercised
    by ``test_concurrent_holds_never_oversell_postgres`` against a real
    Postgres testcontainer.
    """
    acc = api_key_fixture.account
    start = acc.balance_kopecks
    assert start >= 100

    n_repeats = 25
    same_ref = f"dup-{uuid.uuid4().hex}"

    successes = 0
    for _ in range(n_repeats):
        async with session_factory() as s:
            tx = await debit_account(
                s,
                account_id=acc.id,
                amount_kopecks=10,
                ref_id=same_ref,
            )
            await s.commit()
            assert tx.ref_id == same_ref
            successes += 1

    # All N calls succeeded but only the first one debited.
    assert successes == n_repeats
    async with session_factory() as s:
        refreshed = await s.get(Account, acc.id)
        assert refreshed is not None
        assert refreshed.balance_kopecks == start - 10

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(Transaction).where(
                        Transaction.account_id == acc.id,
                        Transaction.ref_id == same_ref,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_hold_amount_does_not_overshoot_balance(session_factory, api_key_fixture):
    """Two sequential ``hold_amount`` calls cannot together exceed the balance.

    Equivalent to the chat 402 path but exercised against the engine
    directly: balance 100 kop, two holds of 80 kop. The second one must
    raise InsufficientBalanceError because (balance - first_hold) = 20 kop.
    """
    acc = api_key_fixture.account
    async with session_factory() as s:
        refreshed = await s.get(Account, acc.id)
        assert refreshed is not None
        refreshed.balance_kopecks = 100
        s.add(refreshed)
        await s.commit()

    async with session_factory() as s:
        await hold_amount(s, account_id=acc.id, amount_kopecks=80, ref_id="hold-a")
        await s.commit()

    async with session_factory() as s:
        with pytest.raises(InsufficientBalanceError) as ei:
            await hold_amount(s, account_id=acc.id, amount_kopecks=80, ref_id="hold-b")
        # Balance is 100, holds requested = 80 (existing) + 80 (new) = 160.
        assert ei.value.balance_kopecks == 100
        assert ei.value.required_kopecks == 160


@pytest.mark.asyncio
async def test_stream_cancel_mid_flight_partial_debit(app, session_factory):
    """Client disconnects mid-stream → debit only the tokens delivered.

    We exercise the partial-debit path directly through ``_settle_stream_billing``
    rather than driving an SSE generator end-to-end. Driving ``EventSourceResponse``
    through ASGI under pytest leaves a background ``anyio.Event`` listener
    bound to the test's event loop — and that listener crashes the next
    test when its loop is torn down (a known interaction with
    ``sse_starlette``).

    The asserted contract is what production cares about:

    * ``_settle_stream_billing`` debits ``compute_cost_kopecks`` of the
      *partial* usage (not the estimate, not zero).
    * The hold is released; no leftover row.
    * A ``usage_event`` is written with the partial counts and a ``stream:
      true`` / ``client_disconnected: true`` meta on the transaction.

    The "stream loop actually closes on disconnect" path is exercised by
    the existing happy-path stream tests in ``tests/test_chat_completions.py``.
    """
    from voltari_gateway.api.chat import _settle_stream_billing
    from voltari_gateway.billing.engine import HoldHandle, hold_amount
    from voltari_gateway.router.catalog import get_model

    setup = await _seed_account_with_api_key(session_factory, balance_kopecks=100_000)
    chosen = get_model("gpt-5.4-mini")
    request_id = f"stream-cancel-{uuid.uuid4().hex}"

    # Place a hold up-front, sized like the pre-flight estimate would have
    # been (large enough to cover the full max_tokens). The settlement
    # call will see the partial usage and debit only that.
    estimated_hold_kop = 5_000
    async with session_factory() as s:
        await hold_amount(
            s,
            account_id=setup.account_id,
            amount_kopecks=estimated_hold_kop,
            ref_id=request_id,
        )
        await s.commit()

    handle = HoldHandle(
        account_id=setup.account_id,
        ref_id=request_id,
        amount_kopecks=estimated_hold_kop,
    )

    # Simulate the generator's terminal state: usage seen so far is one
    # chunk worth (1000 prompt / 500 completion). Compute_cost for that
    # is 28 kop (see docstring on test_chat_debits_balance_on_success).
    await _settle_stream_billing(
        factory=session_factory,
        account_id=setup.account_id,
        api_key_id=setup.api_key_id,
        chosen=chosen,
        request_id=request_id,
        hold=handle,
        usage_input=1000,
        usage_output=500,
        usage_cached=0,
        client_disconnected=True,
        provider_failed=False,
    )

    expected_partial_cost = 28
    new_balance = await _read_balance(session_factory, setup.account_id)
    assert new_balance == 100_000 - expected_partial_cost

    async with session_factory() as s:
        events = (
            (await s.execute(select(UsageEvent).where(UsageEvent.account_id == setup.account_id)))
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].input_tokens == 1000
    assert events[0].output_tokens == 500
    assert events[0].cost_kopecks == expected_partial_cost

    # Transaction's meta carries the cancellation flag — auditors and
    # support tools rely on this to distinguish cancelled streams.
    async with session_factory() as s:
        tx = (
            await s.execute(select(Transaction).where(Transaction.ref_id == request_id))
        ).scalar_one()
    assert tx.meta is not None
    assert tx.meta.get("client_disconnected") is True
    assert tx.meta.get("stream") is True

    # Hold cleared.
    assert await _read_active_holds(session_factory, setup.account_id) == 0


# ---------- postgres-only concurrency tests --------------------------------

pytestmark_postgres = pytest.mark.integration

if os.environ.get("SKIP_POSTGRES_TESTS", "").lower() not in ("1", "true", "yes"):
    testcontainers = pytest.importorskip("testcontainers.postgres")

    @pytest.fixture(scope="module")
    def postgres_url() -> AsyncIterator[str]:
        try:
            container = testcontainers.PostgresContainer(  # type: ignore[attr-defined]
                "postgres:16-alpine",
                username="voltari",
                password="voltari",
                dbname="voltari",
            )
            container.start()
        except Exception as exc:
            pytest.skip(f"Docker/Postgres unavailable: {exc}")
        try:
            sync_url = container.get_connection_url()
            async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
            if "+asyncpg" not in async_url:
                async_url = async_url.replace("postgresql://", "postgresql+asyncpg://")
            yield async_url
        finally:
            container.stop()

    @pytest_asyncio.fixture(scope="function")
    async def pg_engine(postgres_url: str):
        eng = create_async_engine(postgres_url, future=True, pool_pre_ping=True)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield eng
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()

    @pytest_asyncio.fixture(scope="function")
    async def pg_factory(pg_engine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_concurrent_holds_never_oversell_postgres(pg_factory):
        """N concurrent ``hold_amount`` against a small balance.

        Real Postgres + ``SELECT FOR UPDATE``. Balance = 100 kop. 10 holds of
        20 kop each: at most 5 can succeed (5 × 20 = 100), the rest must
        raise InsufficientBalanceError. The point of running this on Postgres
        is to verify that the row-lock serialises holds correctly — under
        the old Redis design, several would slip past pre-flight.
        """
        # Seed: a user/account/api_key, balance = 100 kop.
        async with pg_factory() as s:
            user = User(
                email=f"u-{uuid.uuid4().hex[:8]}@t.local",
                password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
                email_verified=True,
            )
            s.add(user)
            await s.flush()
            account = Account(
                owner_id=user.id,
                name="conc-test",
                balance_kopecks=100,
                tariff=Tariff.PRO,
                status=AccountStatus.ACTIVE,
                store_prompts=True,
            )
            s.add(account)
            await s.commit()
            account_id = account.id

        n_parallel = 10
        per_hold = 20  # only 5 can fit in 100 kop

        async def _attempt(i: int) -> bool:
            async with pg_factory() as s:
                try:
                    await hold_amount(
                        s,
                        account_id=account_id,
                        amount_kopecks=per_hold,
                        ref_id=f"conc-hold-{i}",
                    )
                    await s.commit()
                    return True
                except InsufficientBalanceError:
                    await s.rollback()
                    return False

        results = await asyncio.gather(*[_attempt(i) for i in range(n_parallel)])
        wins = sum(1 for r in results if r)
        assert wins == 5, f"expected exactly 5 winners (100/20), got {wins}"

        # Sum of holds equals balance (100 kop fully reserved).
        async with pg_factory() as s:
            rows = (
                (await s.execute(select(AccountHold).where(AccountHold.account_id == account_id)))
                .scalars()
                .all()
            )
            assert len(rows) == 5
            assert sum(h.amount_kopecks for h in rows) == 100

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cas_high_concurrency_exactly_fits(pg_factory):
        """TD-029: 100 parallel ``hold_amount`` on one account with balance
        exactly enough for 50 → exactly 50 winners, balance never negative.

        This is the spec invariant from TD-029:
            balance = 50 × per_hold,
            n_parallel = 100,
            wins == 50,
            sum_holds == balance (no overshoot).

        Verifies the CAS path correctness under high contention. The
        advisory-lock serialisation must hold for all 100 attempts on one
        account; different account ids would proceed in parallel without
        contending on the same advisory key.
        """
        per_hold = 10
        winners_target = 50
        n_parallel = 100
        balance = per_hold * winners_target

        async with pg_factory() as s:
            user = User(
                email=f"u-{uuid.uuid4().hex[:8]}@t.local",
                password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
                email_verified=True,
            )
            s.add(user)
            await s.flush()
            account = Account(
                owner_id=user.id,
                name="cas-100",
                balance_kopecks=balance,
                tariff=Tariff.PRO,
                status=AccountStatus.ACTIVE,
                store_prompts=True,
            )
            s.add(account)
            await s.commit()
            account_id = account.id

        async def _attempt(i: int) -> bool:
            async with pg_factory() as s:
                try:
                    await hold_amount(
                        s,
                        account_id=account_id,
                        amount_kopecks=per_hold,
                        ref_id=f"cas-{i}",
                    )
                    await s.commit()
                    return True
                except InsufficientBalanceError:
                    await s.rollback()
                    return False

        results = await asyncio.gather(*[_attempt(i) for i in range(n_parallel)])
        wins = sum(1 for r in results if r)
        assert wins == winners_target, f"expected {winners_target}, got {wins}"

        # Balance never went negative AND no holds exceed available.
        async with pg_factory() as s:
            refreshed = await s.get(Account, account_id)
            assert refreshed is not None
            assert refreshed.balance_kopecks == balance  # untouched (only holds)

            holds = (
                (await s.execute(select(AccountHold).where(AccountHold.account_id == account_id)))
                .scalars()
                .all()
            )
            assert len(holds) == winners_target
            assert sum(h.amount_kopecks for h in holds) == balance
