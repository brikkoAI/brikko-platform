"""Shared fixtures for router tests.

We deliberately avoid importing FastAPI / DB / Redis here — the router
is a pure module and these tests should run in <1s with zero external
dependencies. Mock providers are simple async callables that emit the
errors we care about; no `respx` needed because failover doesn't talk
HTTP — it talks to a generic `ProviderCallable`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from voltari_gateway.router.catalog import (
    ModelSpec,
    ModelTier,
    Provider,
)
from voltari_gateway.router.failover import (
    ProviderAuthError,
    ProviderCallable,
    ProviderClientError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from voltari_gateway.router.router import AccountContext, Router

# ---------------------------------------------------------------------------
# Tiny synthetic catalogue for strategy tests. Real catalogue is also
# valid; we use this one when we need exhaustive ordering assertions.
# ---------------------------------------------------------------------------


def _spec(
    *,
    id: str,
    provider: Provider,
    tier: ModelTier,
    inp: int,
    out: int,
    ctx: int = 128_000,
    p50: int = 1_000,
    quality: int = 70,
    ru_legal: bool = False,
    tools: bool = True,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=provider,
        tier=tier,
        input_price_kop_per_1k=inp,
        cached_price_kop_per_1k=inp // 10 or 1,
        output_price_kop_per_1k=out,
        context_window=ctx,
        latency_p50_ms=p50,
        quality_score=quality,
        supports_streaming=True,
        supports_tools=tools,
        ru_legal=ru_legal,
    )


@pytest.fixture
def synthetic_catalog() -> tuple[ModelSpec, ...]:
    """Compact catalogue with known cost/latency/quality for assertions."""
    return (
        _spec(  # cheapest, lowest quality
            id="cheap-1",
            provider=Provider.DEEPSEEK,
            tier=ModelTier.NANO,
            inp=2,
            out=3,
            quality=60,
            p50=1_500,
        ),
        _spec(
            id="cheap-2-openai",
            provider=Provider.OPENAI,
            tier=ModelTier.BUDGET,
            inp=5,
            out=10,
            quality=72,
            p50=900,
        ),
        _spec(  # mid tier — best smart score
            id="mid-1",
            provider=Provider.ANTHROPIC,
            tier=ModelTier.MID,
            inp=20,
            out=100,
            quality=85,
            p50=1_300,
        ),
        _spec(
            id="mid-2",
            provider=Provider.GOOGLE,
            tier=ModelTier.MID,
            inp=15,
            out=80,
            quality=78,
            p50=600,  # fastest
        ),
        _spec(  # premium — should be excluded by smart for plain chat
            id="premium-1",
            provider=Provider.OPENAI,
            tier=ModelTier.PREMIUM,
            inp=100,
            out=400,
            quality=95,
            p50=4_000,
        ),
        _spec(  # RU-legal Yandex
            id="yandex-1",
            provider=Provider.YANDEX,
            tier=ModelTier.MID,
            inp=40,
            out=40,
            quality=65,
            p50=1_000,
            ru_legal=True,
            ctx=32_000,
            tools=False,
        ),
        _spec(  # RU-legal Sber
            id="sber-1",
            provider=Provider.SBER,
            tier=ModelTier.BUDGET,
            inp=6,
            out=6,
            quality=55,
            p50=900,
            ru_legal=True,
            ctx=131_000,
            tools=False,
        ),
        _spec(  # large-context for long-context tests
            id="long-ctx-1",
            provider=Provider.GOOGLE,
            tier=ModelTier.MID,
            inp=10,
            out=80,
            quality=80,
            p50=1_200,
            ctx=1_000_000,
        ),
    )


@pytest.fixture
def router(synthetic_catalog: tuple[ModelSpec, ...]) -> Router:
    return Router(catalog=synthetic_catalog)


# Static UUIDs so log assertions stay stable across test runs.
_ACCOUNT_PAYG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_ACCOUNT_RU_LEGAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_ACCOUNT_BUDGET_ID = uuid.UUID("00000000-0000-0000-0000-00000000002a")


@pytest.fixture
def account_payg() -> AccountContext:
    return AccountContext(
        account_id=_ACCOUNT_PAYG_ID,
        tariff="payg",
        balance_kop=50_000,
    )


@pytest.fixture
def account_ru_legal() -> AccountContext:
    return AccountContext(
        account_id=_ACCOUNT_RU_LEGAL_ID,
        tariff="business",
        balance_kop=1_000_000,
        require_ru_legal=True,
    )


# ---------------------------------------------------------------------------
# Mock provider callables for failover tests.
# ---------------------------------------------------------------------------


class CallRecorder:
    """Records each invocation so tests can assert call sequence + count."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, model: ModelSpec) -> str:
        self.calls.append(model.id)
        return model.id


def make_call_that_fails_then_succeeds(
    fail_count: int,
    error_factory: Callable[[], Exception] = lambda: ProviderServerError("boom", status_code=500),
    *,
    success_value: Any = "ok",
    latency_ms: int = 42,
    recorder: CallRecorder | None = None,
) -> ProviderCallable[Any]:
    """Build a callable that raises N times before succeeding."""
    state = {"calls": 0}

    async def _call(model: ModelSpec) -> tuple[Any, int]:
        if recorder is not None:
            recorder(model)
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise error_factory()
        return success_value, latency_ms

    return _call


def make_call_that_fails_for_models(
    failing_model_ids: set[str],
    error_factory: Callable[[], Exception] = lambda: ProviderServerError("boom", status_code=500),
    *,
    success_value: Any = "ok",
    latency_ms: int = 42,
    recorder: CallRecorder | None = None,
) -> ProviderCallable[Any]:
    """Build a callable that always fails for given models, succeeds for others."""

    async def _call(model: ModelSpec) -> tuple[Any, int]:
        if recorder is not None:
            recorder(model)
        if model.id in failing_model_ids:
            raise error_factory()
        return success_value, latency_ms

    return _call


def make_always_failing_call(
    error_factory: Callable[[], Exception] = lambda: ProviderServerError("boom", status_code=500),
    *,
    recorder: CallRecorder | None = None,
) -> ProviderCallable[Any]:
    async def _call(model: ModelSpec) -> tuple[Any, int]:
        if recorder is not None:
            recorder(model)
        raise error_factory()

    return _call


def make_slow_call(
    sleep_s: float,
    *,
    recorder: CallRecorder | None = None,
) -> ProviderCallable[Any]:
    """Sleeps longer than the failover timeout — used to test timeout path."""

    async def _call(model: ModelSpec) -> tuple[Any, int]:
        if recorder is not None:
            recorder(model)
        await asyncio.sleep(sleep_s)
        return "ok", 1

    return _call


# Re-export error classes so tests can import them from conftest convenient
__all__ = [
    "CallRecorder",
    "ProviderAuthError",
    "ProviderClientError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderServerError",
    "ProviderTimeoutError",
    "make_always_failing_call",
    "make_call_that_fails_for_models",
    "make_call_that_fails_then_succeeds",
    "make_slow_call",
]
