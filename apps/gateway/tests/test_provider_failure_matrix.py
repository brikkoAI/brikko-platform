"""QA P0-10 — Provider 5xx + timeout matrix.

Покрывает все 6 провайдеров (OpenAI, Anthropic, Google, DeepSeek, Yandex,
Sber) × 5 типов ошибок (429, 503, network reset, timeout, malformed JSON).

Стратегия: тестируем `with_failover` как единый failover-engine — каждый
адаптер транслирует свои нативные ошибки в `Provider*Error`-таксономию
(см. failover.py:49). Тут проверяем, что failover engine правильно
обрабатывает КАЖДЫЙ из 5 типов ошибок:
    - retries within model
    - failover to next model
    - cost_kopecks=0 если все упали
    - не двойное списание

Это unit-уровень. HTTP-mock на каждый адаптер уже покрыт в
test_*_provider.py и был бы дублированием — те тесты гарантируют, что
адаптер ПРАВИЛЬНО МАППИТ свой нативный 503/429/timeout в Provider*Error.
Здесь мы доверяем этому маппингу и проверяем engine.

Для интеграции "all 6 fall → 502 + balance не списан + hold released"
используется существующий /v1/chat-flow с `app.state.provider_registry`
населённой failing-адаптерами всех 6 провайдеров.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import select

from voltari_gateway.db.models import (
    Account,
    AccountHold,
    Transaction,
    UsageEvent,
)
from voltari_gateway.providers.base import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    Provider,
)
from voltari_gateway.router.catalog import (
    ModelSpec,
    get_model,
)
from voltari_gateway.router.catalog import (
    Provider as ProviderEnum,
)
from voltari_gateway.router.failover import (
    FailoverError,
    ProviderAuthError,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
    with_failover,
)

# ---------------------------------------------------------------------------
# Test-helper provider
# ---------------------------------------------------------------------------


class ScriptedProvider(Provider):
    """Provider that raises on the first N attempts then returns a response.

    Used to test "retry within model exhausts then moves to failover".
    """

    def __init__(
        self,
        name: str,
        failures: list[Exception],
        success_response: ChatCompletionResponse | None = None,
    ) -> None:
        self.name = name
        self._failures = list(failures)
        self._success = success_response
        self.calls = 0

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        self.calls += 1
        if self._failures:
            exc = self._failures.pop(0)
            raise exc
        if self._success is None:
            return ChatCompletionResponse(
                raw={
                    "id": "ok",
                    "object": "chat.completion",
                    "created": 1,
                    "model": req.model.id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                model_id=req.model.id,
                provider=self.name,
            )
        return self._success

    async def chat_completion_stream(self, req: ChatCompletionRequest):
        async def _empty() -> AsyncIterator[bytes]:
            if False:
                yield b""

        if self._failures:
            exc = self._failures.pop(0)
            raise exc
        return _empty()

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Per-provider model under test (one representative model per provider).
# ---------------------------------------------------------------------------


_PRIMARY_MODEL_BY_PROVIDER: dict[ProviderEnum, str] = {
    ProviderEnum.OPENAI: "gpt-5.4-mini",
    ProviderEnum.ANTHROPIC: "claude-haiku-4.5",
    ProviderEnum.GOOGLE: "gemini-3-flash",
    ProviderEnum.DEEPSEEK: "deepseek-v3.2-chat",
    ProviderEnum.YANDEX: "yandexgpt-5-lite",
    ProviderEnum.SBER: "gigachat-2-lite",
}


def _model(provider: ProviderEnum) -> ModelSpec:
    spec = get_model(_PRIMARY_MODEL_BY_PROVIDER[provider])
    assert spec is not None
    return spec


# Each scenario is (label, exception_factory). The exception_factory takes
# nothing and returns a fresh `ProviderError` subclass instance — fresh
# because exceptions can't be reused across raises in a single coroutine
# stack (PEP 657 traceback context).
_FAILURE_SCENARIOS: list[tuple[str, Callable[[], ProviderError]]] = [
    (
        "rate_limit_429",
        lambda: ProviderRateLimitError("429 rate limit", retry_after_s=1.0, status_code=429),
    ),
    (
        "server_error_503",
        lambda: ProviderServerError("503 service unavailable", status_code=503),
    ),
    (
        "network_reset",
        lambda: ProviderTimeoutError("network connection reset"),
    ),
    (
        "slow_loris_timeout",
        lambda: ProviderTimeoutError("upstream timeout"),
    ),
    (
        "malformed_json",
        # "Malformed JSON" is observed at the adapter layer — by the time it
        # reaches `with_failover` it's already a `ProviderServerError` (the
        # adapter's standard catch-all for unparseable upstream responses,
        # since the kind that hangs up the bytes after a 200 is morally a
        # 5xx). Generic ``ProviderError`` short-circuits failover to the
        # NEXT model immediately (failover.py:306), which is a different
        # contract — out of scope for this matrix.
        lambda: ProviderServerError("malformed JSON in upstream response", status_code=502),
    ),
]


# ---------------------------------------------------------------------------
# Per-provider × per-error tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", list(_PRIMARY_MODEL_BY_PROVIDER.keys()))
@pytest.mark.parametrize(("scenario_label", "factory"), _FAILURE_SCENARIOS)
@pytest.mark.asyncio
async def test_provider_error_triggers_failover_to_next(
    provider: ProviderEnum, scenario_label: str, factory: Callable[[], ProviderError]
) -> None:
    """Per-provider × per-error: primary fails N times → failover to next.

    Constructs:
      - primary = ScriptedProvider that raises on EVERY attempt within
        the failover engine's backoff schedule.
      - fallback = ScriptedProvider that succeeds.
    Verifies:
      - failover_used=True
      - fallback was called
      - primary was hit `len(backoffs_ms)` times before giving up.
    """
    primary_model = _model(provider)

    # Pick fallback model from a DIFFERENT provider so the chain crosses providers.
    fallback_provider = next(p for p in _PRIMARY_MODEL_BY_PROVIDER if p != provider)
    fallback_model = _model(fallback_provider)

    # Tight backoffs for fast tests.
    backoffs = (1, 1, 1)
    primary = ScriptedProvider(
        name=str(provider),
        failures=[factory() for _ in range(len(backoffs))],
    )
    fallback = ScriptedProvider(name=str(fallback_provider), failures=[])

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        if model.id == primary_model.id:
            return await primary.chat_completion(_make_req(model)), 1
        if model.id == fallback_model.id:
            return await fallback.chat_completion(_make_req(model)), 1
        raise AssertionError(f"unexpected model in chain: {model.id}")

    result = await with_failover(
        primary=primary_model,
        fallback_chain=[fallback_model],
        call=_call,
        backoffs_ms=backoffs,
        timeout_s=1.0,
    )

    assert result.failover_used is True, (
        f"{provider.value}/{scenario_label}: failover_used должен быть True"
    )
    assert result.model.id == fallback_model.id
    assert primary.calls == len(backoffs), (
        f"primary должен был получить {len(backoffs)} попыток, получил {primary.calls}"
    )
    assert fallback.calls == 1


# ---------------------------------------------------------------------------
# All providers fail → FailoverError raised → upstream maps to 502
# (chat handler-уровень)
# ---------------------------------------------------------------------------


def _make_req(model: ModelSpec) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[{"role": "user", "content": "test"}],
    )


@pytest.mark.asyncio
async def test_all_providers_fail_raises_failover_error_with_last_error() -> None:
    """All providers in chain fail → FailoverError carries last_error.

    Прямая проверка контракта engine: при exhaustion engine RAISES
    FailoverError, а не возвращает результат с failover_used=True.
    """
    primary = _model(ProviderEnum.OPENAI)
    fallback1 = _model(ProviderEnum.ANTHROPIC)
    fallback2 = _model(ProviderEnum.GOOGLE)

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        raise ProviderServerError(f"upstream {model.provider} is on fire", status_code=503)

    with pytest.raises(FailoverError) as exc_info:
        await with_failover(
            primary=primary,
            fallback_chain=[fallback1, fallback2],
            call=_call,
            backoffs_ms=(1, 1, 1),
            timeout_s=1.0,
        )

    assert exc_info.value.last_error is not None
    assert isinstance(exc_info.value.last_error, ProviderServerError)
    # Each model gets `len(backoffs_ms)` attempts before moving on.
    assert len(exc_info.value.attempts) == 3 * 3, (
        f"3 models × 3 attempts = 9 events expected, got {len(exc_info.value.attempts)}"
    )
    # Every event should be failed.
    assert all(not e.succeeded for e in exc_info.value.attempts)


# ---------------------------------------------------------------------------
# Auth error (401/403) DOES NOT retry — jumps straight to failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_auth_error_skips_retries_jumps_to_next() -> None:
    """ProviderAuthError → no retries on this model, immediate failover.

    Compromised key: retrying on the same provider can't help. The engine
    must NOT exhaust the backoff schedule.
    """
    primary = _model(ProviderEnum.OPENAI)
    fallback = _model(ProviderEnum.ANTHROPIC)

    primary_calls = 0
    fallback_calls = 0

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        nonlocal primary_calls, fallback_calls
        if model.id == primary.id:
            primary_calls += 1
            raise ProviderAuthError("401 unauthorized", status_code=401)
        fallback_calls += 1
        return ChatCompletionResponse(
            raw={
                "id": "ok",
                "object": "chat.completion",
                "created": 1,
                "model": model.id,
                "choices": [],
                "usage": {},
            },
            usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            model_id=model.id,
            provider=str(model.provider),
        ), 1

    result = await with_failover(
        primary=primary,
        fallback_chain=[fallback],
        call=_call,
        backoffs_ms=(1, 1, 1),
        timeout_s=1.0,
    )

    # Primary должен получить РОВНО 1 вызов — не retry'ится.
    assert primary_calls == 1, (
        f"AuthError должен skip retry (примерно 1 вызов), получили {primary_calls}"
    )
    assert fallback_calls == 1
    assert result.failover_used is True


# ---------------------------------------------------------------------------
# 4xx (client error) — DO NOT retry, DO NOT failover, propagate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_error_400_propagates_without_failover() -> None:
    """4xx (e.g. 400 bad_request) — caller's fault; failover не помогает.

    Контракт failover.py:225 — ProviderClientError re-raised unchanged.
    """
    primary = _model(ProviderEnum.OPENAI)
    fallback = _model(ProviderEnum.ANTHROPIC)

    fallback_calls = 0

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        nonlocal fallback_calls
        if model.id == primary.id:
            raise ProviderClientError("400 bad request", status_code=400)
        fallback_calls += 1
        raise AssertionError("fallback не должен был вызваться при 4xx")

    with pytest.raises(ProviderClientError):
        await with_failover(
            primary=primary,
            fallback_chain=[fallback],
            call=_call,
            backoffs_ms=(1, 1, 1),
            timeout_s=1.0,
        )

    assert fallback_calls == 0


# ---------------------------------------------------------------------------
# Slow loris / wait_for timeout — engine MUST cancel & treat as timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_loris_provider_cancelled_and_failed_over() -> None:
    """Provider hangs → engine timeout cancels & treats as ProviderTimeoutError.

    Никакого вечного hang'а. Engine использует asyncio.wait_for с
    timeout_s, после чего эмитит ProviderTimeoutError и идёт дальше.
    """
    primary = _model(ProviderEnum.OPENAI)
    fallback = _model(ProviderEnum.ANTHROPIC)

    primary_call_started = False

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        nonlocal primary_call_started
        if model.id == primary.id:
            primary_call_started = True
            # Hang forever (in real scenario это slow loris).
            await asyncio.sleep(60)
            raise AssertionError("должен был быть cancelled timeout'ом")
        return ChatCompletionResponse(
            raw={
                "id": "ok",
                "object": "chat.completion",
                "created": 1,
                "model": model.id,
                "choices": [],
                "usage": {},
            },
            usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            model_id=model.id,
            provider=str(model.provider),
        ), 1

    # Tight timeout — 100ms — чтобы тест выполнялся быстро.
    result = await with_failover(
        primary=primary,
        fallback_chain=[fallback],
        call=_call,
        backoffs_ms=(1,),  # один attempt чтобы не ждать N×timeout
        timeout_s=0.1,
    )

    assert primary_call_started, "primary должен был стартовать"
    assert result.failover_used is True
    assert result.model.id == fallback.id


# ---------------------------------------------------------------------------
# Failover не вызывает успешный provider дважды (no double-charge)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failover_does_not_call_success_twice() -> None:
    """Primary fails, secondary succeeds → secondary called ровно 1 раз.

    Фундаментальная инвариант: engine returns на первом успехе, не
    идёт дальше по chain'у.
    """
    primary = _model(ProviderEnum.OPENAI)
    fallback1 = _model(ProviderEnum.ANTHROPIC)
    fallback2 = _model(ProviderEnum.GOOGLE)

    fallback1_calls = 0
    fallback2_calls = 0

    async def _call(model: ModelSpec) -> tuple[ChatCompletionResponse, int]:
        nonlocal fallback1_calls, fallback2_calls
        if model.id == primary.id:
            raise ProviderServerError("503", status_code=503)
        if model.id == fallback1.id:
            fallback1_calls += 1
            return ChatCompletionResponse(
                raw={
                    "id": "ok",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model.id,
                    "choices": [],
                    "usage": {},
                },
                usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
                model_id=model.id,
                provider=str(model.provider),
            ), 1
        # fallback2 не должен ever-be-called.
        fallback2_calls += 1
        raise AssertionError("fallback2 вызвался — engine ушёл слишком далеко")

    result = await with_failover(
        primary=primary,
        fallback_chain=[fallback1, fallback2],
        call=_call,
        backoffs_ms=(1,),
        timeout_s=1.0,
    )

    assert fallback1_calls == 1
    assert fallback2_calls == 0
    assert result.model.id == fallback1.id


# ---------------------------------------------------------------------------
# Integration: all 6 providers fail в /v1/chat → 502 + cost_kop=0 +
# balance не списан + hold released
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_failed_returns_502_balance_unchanged(
    client, api_key_fixture, app, db, session_factory
) -> None:
    """Все 6 провайдеров вернули 5xx → итоговый 502 + cost_kop=0 + balance ОСТАЛСЯ.

    Покрывает: hold release при failure (см. Sprint 1 P0-4), нет
    usage_event с cost > 0, нет дополнительной transaction.
    """
    from voltari_gateway.providers.registry import ProviderRegistry

    initial_balance = api_key_fixture.account.balance_kopecks

    # Заменим все 6 провайдеров на failing-stub'ы.
    failing = ScriptedProvider(
        name="all-failing",
        failures=[ProviderServerError("503 fire", status_code=503)] * 100,
    )
    registry = ProviderRegistry()
    for prov_enum in ProviderEnum:
        registry.register(prov_enum, failing)
    app.state.provider_registry = registry

    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)

    assert r.status_code == 502, f"all-fail должен дать 502, got {r.status_code}: {r.text}"

    # Используем свежую сессию для чтения post-handler state.
    async with session_factory() as fresh:
        acc = await fresh.get(Account, api_key_fixture.account.id)
        assert acc is not None
        assert acc.balance_kopecks == initial_balance, (
            f"balance changed несмотря на full failure! "
            f"before={initial_balance}, after={acc.balance_kopecks}"
        )

        # Active hold'ы должны быть released.
        holds = (
            (
                await fresh.execute(
                    select(AccountHold).where(AccountHold.account_id == api_key_fixture.account.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(holds) == 0, f"hold не освобождён: {holds}"

        # Usage events: либо нет, либо cost_kop=0.
        events = (
            (
                await fresh.execute(
                    select(UsageEvent).where(UsageEvent.account_id == api_key_fixture.account.id)
                )
            )
            .scalars()
            .all()
        )
        if events:
            assert all(e.cost_kopecks == 0 for e in events), (
                f"usage_event с cost>0 при full failure: {[(e.model, e.cost_kopecks) for e in events]}"
            )

        # Никаких CHARGE-транзакций.
        txs = (
            (
                await fresh.execute(
                    select(Transaction).where(Transaction.account_id == api_key_fixture.account.id)
                )
            )
            .scalars()
            .all()
        )
        charges = [t for t in txs if t.type.value == "charge"]
        assert len(charges) == 0, f"charge transaction при full failure: {charges}"


@pytest.mark.asyncio
async def test_failover_does_not_double_charge(
    client, api_key_fixture, app, db, session_factory
) -> None:
    """Primary fails, fallback succeeds → один CHARGE, не два."""
    from voltari_gateway.providers.registry import ProviderRegistry

    initial_balance = api_key_fixture.account.balance_kopecks

    failing_openai = ScriptedProvider(
        name="openai-fail",
        failures=[ProviderServerError("503", status_code=503)] * 100,
    )
    succeeding_anthropic = ScriptedProvider(
        name="anthropic-ok",
        failures=[],
        success_response=ChatCompletionResponse(
            raw={
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 1,
                "model": "claude-haiku-4.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
            usage=ChatCompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model_id="claude-haiku-4.5",
            provider="anthropic",
        ),
    )

    registry = ProviderRegistry()
    registry.register(ProviderEnum.OPENAI, failing_openai)
    registry.register(ProviderEnum.ANTHROPIC, succeeding_anthropic)
    app.state.provider_registry = registry

    body = {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "ping"}],
    }
    r = await client.post("/v1/chat/completions", json=body, headers=api_key_fixture.auth_header)
    assert r.status_code == 200, r.text

    # Используем свежую сессию для чтения — `db` фикстура держит свою
    # прокешированную сессию, которая не видит коммитов из API-handler'а.
    async with session_factory() as fresh:
        txs = (
            (
                await fresh.execute(
                    select(Transaction).where(Transaction.account_id == api_key_fixture.account.id)
                )
            )
            .scalars()
            .all()
        )
        charges = [t for t in txs if t.type.value == "charge"]
        assert len(charges) == 1, (
            f"должна быть РОВНО 1 charge tx (failover не должен дважды списать), "
            f"got {len(charges)}: {[(c.amount_kopecks, c.ref_id) for c in charges]}"
        )

        # Balance уменьшился ровно на сумму этой транзакции.
        acc = await fresh.get(Account, api_key_fixture.account.id)
        assert acc is not None
        expected_decrease = abs(charges[0].amount_kopecks)
        assert acc.balance_kopecks == initial_balance - expected_decrease, (
            f"balance change != amount списания. "
            f"initial={initial_balance}, current={acc.balance_kopecks}, charge={expected_decrease}"
        )


# ---------------------------------------------------------------------------
# Circuit breaker — placeholder, depends on Поток D (BE P1-20)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_skips_open_provider() -> None:
    """Circuit breaker (BE P1-20) — open provider должен skip'аться.

    Реализовано в Sprint 2 Поток D. Подробные тесты в test_circuit_breaker.py;
    здесь оставлен smoke-passing-stub чтобы матрица провайдер-failures видела
    "CB присутствует". Полные сценарии (open / half-open / per-provider) в
    test_circuit_breaker.py.
    """
    import fakeredis.aioredis

    from voltari_gateway.router.circuit_breaker import (
        CircuitBreaker,
        CircuitConfig,
        CircuitState,
    )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cb = CircuitBreaker(redis, config=CircuitConfig(threshold=2, window_seconds=2, reset_seconds=1))
    await cb.record_error("openai")
    await cb.record_error("openai")
    assert await cb.state("openai") == CircuitState.OPEN
    await redis.aclose()
