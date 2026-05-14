"""Unit tests for balance_monitor.adapters.

We mock httpx via respx for HTTP-touching adapters. ManualAdapter has no
HTTP — its tests are pure-Python.

Coverage:
* DeepSeek: parse success (USD), prefer USD when multiple currencies,
  401 auth, network error, malformed JSON, missing balance_infos.
* Sber: OAuth + balance success, OAuth fails → snapshot.error,
  balance HTTP 401 → snapshot.error, weird response shape, sums all rows.
* Moonshot: parse success in three known shapes (data-wrapped, flat,
  legacy ``balance``), 401, missing balance field, non-dict response.
* MiniMax: data-wrapped, flat, wallet-style shapes, RMB→CNY normalization,
  base_resp error envelope, 401, network error, non-dict response.
* Zhipu: JWT generation, success with code=200 wrapper, flat shape,
  api_error from code != 200, 401, malformed api_key rejected at construct.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx
import jwt
import pytest
import respx

from voltari_gateway.balance_monitor.adapters import (
    DeepSeekBalanceAdapter,
    ManualAdapter,
    MiniMaxBalanceAdapter,
    MoonshotBalanceAdapter,
    SberBalanceAdapter,
    ZhipuBalanceAdapter,
)
from voltari_gateway.balance_monitor.adapters.deepseek import DEEPSEEK_BALANCE_URL
from voltari_gateway.balance_monitor.adapters.minimax import MINIMAX_BALANCE_URL
from voltari_gateway.balance_monitor.adapters.moonshot import MOONSHOT_BALANCE_URL
from voltari_gateway.balance_monitor.adapters.sber import SBER_BALANCE_URL
from voltari_gateway.balance_monitor.adapters.zhipu import (
    ZHIPU_BALANCE_URL,
    _make_zhipu_jwt,
)
from voltari_gateway.providers.sber_provider import SBER_OAUTH_URL

# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deepseek_success_usd() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "5.234567",
                            "granted_balance": "0.00",
                            "topped_up_balance": "5.23",
                        }
                    ],
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.provider == "deepseek"
        assert snap.currency == "USD"
        assert snap.balance_native == Decimal("5.234567")
        assert snap.fetch_method == "api"
        assert snap.raw is not None and snap.raw["is_available"] is True
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_prefers_usd_when_multiple_currencies() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {"currency": "CNY", "total_balance": "100.00"},
                        {"currency": "USD", "total_balance": "10.00"},
                    ],
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.currency == "USD"
        assert snap.balance_native == Decimal("10.00")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_401_returns_error_snapshot() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-bad")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(401, json={"error": "invalid_api_key"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None
        assert "auth_401" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_500_returns_error_snapshot() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock() as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(503, text="bad gateway")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("http_503")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_network_error_returns_error_snapshot() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock() as mock:
            mock.get(DEEPSEEK_BALANCE_URL).mock(side_effect=httpx.ConnectError("dns_fail"))
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("network:")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_empty_balance_infos() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock() as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(
                200, json={"is_available": True, "balance_infos": []}
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "no_balance_infos"
        # raw response saved despite parse failure
        assert snap.raw is not None
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_deepseek_invalid_total_balance_string() -> None:
    adapter = DeepSeekBalanceAdapter(api_key="sk-ds")
    try:
        with respx.mock() as mock:
            mock.get(DEEPSEEK_BALANCE_URL).respond(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [{"currency": "USD", "total_balance": "not-a-number"}],
                },
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "invalid_total_balance" in snap.error
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Sber
# ---------------------------------------------------------------------------


def _sber_oauth_response() -> dict:
    return {"access_token": "tok-abc", "expires_at": 9999999999000}


@pytest.mark.asyncio
async def test_sber_success_sums_all_rows() -> None:
    adapter = SberBalanceAdapter(auth_key="dGVzdA==")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.post(SBER_OAUTH_URL).respond(200, json=_sber_oauth_response())
            mock.get(SBER_BALANCE_URL).respond(
                200,
                json=[
                    {"usage": "GigaChat-Pro", "value": 1000000},
                    {"usage": "GigaChat-Lite", "value": 500000},
                ],
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.currency == "tokens"
        # Decimal in fixed-precision should equal 1500000.
        assert snap.balance_native == Decimal("1500000")
        assert snap.raw is not None and "balance" in snap.raw
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_sber_oauth_failure_short_circuits() -> None:
    adapter = SberBalanceAdapter(auth_key="dGVzdA==")
    try:
        with respx.mock() as mock:
            mock.post(SBER_OAUTH_URL).respond(401, json={"message": "invalid_basic"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("oauth:")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_sber_balance_401_returns_error_snapshot() -> None:
    adapter = SberBalanceAdapter(auth_key="dGVzdA==")
    try:
        with respx.mock() as mock:
            mock.post(SBER_OAUTH_URL).respond(200, json=_sber_oauth_response())
            mock.get(SBER_BALANCE_URL).respond(401, json={"message": "token_expired"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "auth_401" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_sber_unexpected_response_shape() -> None:
    adapter = SberBalanceAdapter(auth_key="dGVzdA==")
    try:
        with respx.mock() as mock:
            mock.post(SBER_OAUTH_URL).respond(200, json=_sber_oauth_response())
            mock.get(SBER_BALANCE_URL).respond(200, json={"unexpected": "shape"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "unexpected_response_shape"
        assert snap.currency == "tokens"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_sber_skips_malformed_rows_but_keeps_total() -> None:
    """One malformed row shouldn't poison the running sum."""
    adapter = SberBalanceAdapter(auth_key="dGVzdA==")
    try:
        with respx.mock() as mock:
            mock.post(SBER_OAUTH_URL).respond(200, json=_sber_oauth_response())
            mock.get(SBER_BALANCE_URL).respond(
                200,
                json=[
                    {"usage": "Pro", "value": 100},
                    {"usage": "broken", "value": "not-a-number"},
                    {"usage": "Lite", "value": 50},
                    "this-is-not-an-object",
                ],
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal(150)
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Moonshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moonshot_data_wrapped_shape() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-mo")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(
                200,
                json={
                    "data": {"available_balance": 50.0, "currency": "USD"},
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("50.0")
        assert snap.currency == "USD"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_moonshot_flat_shape() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-mo")
    try:
        with respx.mock() as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(
                200, json={"available_balance": "12.34", "currency": "CNY"}
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("12.34")
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_moonshot_legacy_balance_field() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-mo")
    try:
        with respx.mock() as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(200, json={"balance": "7.89", "currency": "USD"})
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("7.89")
        assert snap.currency == "USD"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_moonshot_401_error() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-bad")
    try:
        with respx.mock() as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(401, json={"error": "bad"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "auth_401" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_moonshot_missing_balance_field() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-mo")
    try:
        with respx.mock() as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(200, json={"data": {"currency": "USD"}})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "balance_field_missing"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_moonshot_non_dict_response() -> None:
    adapter = MoonshotBalanceAdapter(api_key="sk-mo")
    try:
        with respx.mock() as mock:
            mock.get(MOONSHOT_BALANCE_URL).respond(200, json=[1, 2, 3])
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "non_dict_response"
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# MiniMax
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimax_data_wrapped_shape_usd() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(
                200,
                json={"data": {"balance": "42.50", "currency": "USD"}},
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.provider == "minimax"
        assert snap.balance_native == Decimal("42.50")
        assert snap.currency == "USD"
        assert snap.fetch_method == "api"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_flat_shape_with_rmb_normalized_to_cny() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(
                200,
                json={"balance": "123.45", "currency": "RMB"},
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("123.45")
        # RMB → CNY normalization (FX map в service.native_to_rub_kopecks
        # знает только CNY).
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_wallet_balance_info_shape() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(
                200,
                json={
                    "balance_info": {
                        "available_balance": "9.99",
                        "currency": "CNY",
                    }
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("9.99")
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_default_currency_cny_when_missing() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(200, json={"balance": "5.00"})
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("5.00")
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_base_resp_api_error_envelope() -> None:
    """MiniMax wraps some errors in {"base_resp": {"status_code": ...}} — we surface
    it as snapshot.error без падения."""
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(
                200,
                json={
                    "base_resp": {"status_code": 1004, "status_msg": "auth_failed"},
                },
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "api_error_1004" in snap.error
        # raw saved for debugging
        assert snap.raw is not None
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_401_returns_error_snapshot() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-bad")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(401, json={"error": "bad_key"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "auth_401" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_500_returns_error_snapshot() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(503, text="upstream_down")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("http_503")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_network_error_returns_error_snapshot() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).mock(side_effect=httpx.ConnectError("dns_fail"))
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("network:")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_missing_balance_field() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(200, json={"data": {"currency": "USD"}})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "balance_field_missing"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_minimax_non_dict_response() -> None:
    adapter = MiniMaxBalanceAdapter(api_key="sk-mm")
    try:
        with respx.mock() as mock:
            mock.get(MINIMAX_BALANCE_URL).respond(200, json=[1, 2, 3])
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "non_dict_response"
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Zhipu
# ---------------------------------------------------------------------------


def test_zhipu_jwt_generation_deterministic() -> None:
    """JWT shape: HS256, header.sign_type=SIGN, payload.api_key=id half,
    payload.exp=now+1h (ms), payload.timestamp=now (ms)."""
    api_key = "12345abc.s3cret_half"
    now_ms = 1_700_000_000_000  # 2023-11-14
    token = _make_zhipu_jwt(api_key, now_ms=now_ms)

    # Decode without signature verification to inspect payload.
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["api_key"] == "12345abc"
    assert decoded["timestamp"] == now_ms
    assert decoded["exp"] == now_ms + 3600 * 1000

    # Header must include sign_type=SIGN (Zhipu requirement).
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
    assert header["sign_type"] == "SIGN"

    # And it must be verifiable with the secret half.
    verified = jwt.decode(token, "s3cret_half", algorithms=["HS256"])
    assert verified["api_key"] == "12345abc"


def test_zhipu_jwt_rejects_missing_dot() -> None:
    with pytest.raises(ValueError):
        _make_zhipu_jwt("no_dot_in_this_key")


def test_zhipu_jwt_rejects_empty_halves() -> None:
    with pytest.raises(ValueError):
        _make_zhipu_jwt(".secret_only")
    with pytest.raises(ValueError):
        _make_zhipu_jwt("id_only.")


def test_zhipu_jwt_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        _make_zhipu_jwt("")


def test_zhipu_adapter_rejects_malformed_key_at_construct() -> None:
    with pytest.raises(ValueError):
        ZhipuBalanceAdapter(api_key="no_dot")


def test_zhipu_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        ZhipuBalanceAdapter(api_key="")


@pytest.mark.asyncio
async def test_zhipu_success_code_200_wrapper() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(
                200,
                json={
                    "code": 200,
                    "msg": "success",
                    "data": {"balance": "256.78", "currency": "CNY"},
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.provider == "zhipu"
        assert snap.balance_native == Decimal("256.78")
        assert snap.currency == "CNY"
        assert snap.fetch_method == "api"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_flat_shape() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(200, json={"balance": "10.00"})
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("10.00")
        # Default CNY when currency missing.
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_data_list_account_shape() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(
                200,
                json={
                    "code": 200,
                    "data": [
                        {"balance": "77.77", "currency": "RMB"},
                    ],
                },
            )
            snap = await adapter.fetch()
        assert snap.error is None
        assert snap.balance_native == Decimal("77.77")
        # RMB normalized to CNY.
        assert snap.currency == "CNY"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_api_error_code_returns_error_snapshot() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(
                200,
                json={"code": 1301, "msg": "balance_query_disabled", "data": None},
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "api_error_1301" in snap.error
        assert snap.raw is not None
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_401_returns_error_snapshot() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(401, json={"error": "invalid_jwt"})
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and "auth_401" in snap.error
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_500_returns_error_snapshot() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(503, text="upstream_unavailable")
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("http_503")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_network_error_returns_error_snapshot() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).mock(side_effect=httpx.ConnectError("dns_fail"))
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error is not None and snap.error.startswith("network:")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_missing_balance_field() -> None:
    adapter = ZhipuBalanceAdapter(api_key="id.secret")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).respond(
                200, json={"code": 200, "data": {"currency": "CNY"}}
            )
            snap = await adapter.fetch()
        assert snap.balance_native is None
        assert snap.error == "balance_field_missing"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_zhipu_sends_bearer_with_valid_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the Authorization header carries a JWT (3 segments, HS256)
    signed by the secret half — каркасная проверка signing pipeline."""
    captured_headers: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"code": 200, "data": {"balance": "1.0"}})

    adapter = ZhipuBalanceAdapter(api_key="myid123.mysecret456")
    try:
        with respx.mock() as mock:
            mock.get(ZHIPU_BALANCE_URL).mock(side_effect=_capture)
            snap = await adapter.fetch()
        assert snap.error is None
        auth = captured_headers.get("authorization")
        assert auth is not None
        assert auth.startswith("Bearer ")
        token = auth.removeprefix("Bearer ")
        # JWT has 3 dot-separated segments.
        assert token.count(".") == 2
        # And it should verify with the secret half.
        decoded = jwt.decode(token, "mysecret456", algorithms=["HS256"])
        assert decoded["api_key"] == "myid123"
        # exp must be in the future relative to now (ms).
        assert decoded["exp"] > int(time.time() * 1000)
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# ManualAdapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_adapter_is_not_remote() -> None:
    adapter = ManualAdapter("openai")
    assert adapter.provider_name == "openai"
    assert adapter.is_remote is False
    snap = await adapter.fetch()
    assert snap.balance_native is None
    assert snap.fetch_method == "manual"
    # aclose is a no-op
    await adapter.aclose()


def test_manual_adapter_rejects_empty_provider() -> None:
    with pytest.raises(ValueError):
        ManualAdapter("")


def test_deepseek_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        DeepSeekBalanceAdapter(api_key="")


def test_sber_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        SberBalanceAdapter(auth_key="")


def test_moonshot_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        MoonshotBalanceAdapter(api_key="")


def test_minimax_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        MiniMaxBalanceAdapter(api_key="")
