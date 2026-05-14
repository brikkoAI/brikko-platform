"""Unit tests for ``voltari_gateway.billing.receipts``.

Exercises both paths of the NPD receipt issuance:

* ЮKassa-Самозанятые — happy path + 4xx fallback.
* lknpd.nalog.ru fallback — auth + receipt issuance + token cache.
* ``issue_payg_receipt`` orchestration: ЮKassa first, lknpd fallback,
  full-failure ``ReceiptError`` envelope.

Pulls ``receipts.py`` from 38% → ~85% coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from voltari_gateway.billing.receipts import (
    LknpdClient,
    LknpdConfig,
    ReceiptError,
    ReceiptIssued,
    fetch_yookassa_receipt,
    issue_payg_receipt,
    wait_for_yookassa_receipt,
)

# ---------- fetch_yookassa_receipt ------------------------------------------


@pytest.mark.asyncio
async def test_fetch_yookassa_receipt_returns_first_item():
    """Successful response returns a normalised ReceiptIssued."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "rcpt_123",
                        "receipt_url": "https://yookassa.ru/check/rcpt_123",
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await fetch_yookassa_receipt(http, "pay_abc")
    assert isinstance(out, ReceiptIssued)
    assert out.receipt_id == "rcpt_123"
    assert out.issuer == "yookassa"


@pytest.mark.asyncio
async def test_fetch_yookassa_receipt_returns_none_when_no_items():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await fetch_yookassa_receipt(http, "pay_abc")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_yookassa_receipt_returns_none_on_4xx():
    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="not found"))
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await fetch_yookassa_receipt(http, "pay_abc")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_yookassa_receipt_swallows_http_error():
    """Network blip → None (caller can retry)."""

    def _raise(req):
        raise httpx.ConnectError("dns failed")

    transport = httpx.MockTransport(_raise)
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await fetch_yookassa_receipt(http, "pay_abc")
    assert out is None


# ---------- wait_for_yookassa_receipt ---------------------------------------


@pytest.mark.asyncio
async def test_wait_for_yookassa_receipt_returns_immediately_on_first_hit():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"items": [{"id": "rcpt_x", "receipt_url": "u"}]})
    )
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await wait_for_yookassa_receipt(
            http, "pay_x", max_wait_seconds=0.01, poll_interval_seconds=0.005
        )
    assert isinstance(out, ReceiptIssued)


@pytest.mark.asyncio
async def test_wait_for_yookassa_receipt_returns_none_on_timeout():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))
    async with httpx.AsyncClient(base_url="https://yk.test/v3", transport=transport) as http:
        out = await wait_for_yookassa_receipt(
            http, "pay_x", max_wait_seconds=0.01, poll_interval_seconds=0.005
        )
    assert out is None


# ---------- LknpdClient -----------------------------------------------------


def _lknpd_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Build a MockTransport that dispatches by URL path."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Strip the base_url prefix.
        path = request.url.path
        for suffix, response in routes.items():
            if path.endswith(suffix):
                return response
        return httpx.Response(404, text=f"unmatched path {path}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_lknpd_client_authenticates_then_caches_token():
    """First call to issue_receipt does auth + income; second call reuses token."""
    auth_calls = 0
    income_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal auth_calls, income_calls
        if request.url.path.endswith("/auth/lkfl"):
            auth_calls += 1
            future = (datetime.now(UTC) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200,
                json={"token": "tok_abc", "refreshToken": "rt_abc", "tokenExpireIn": future},
            )
        if request.url.path.endswith("/income"):
            income_calls += 1
            return httpx.Response(200, json={"approvedReceiptUuid": "rcpt_lknpd_1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://lknpd.nalog.ru/api/v1",
        transport=transport,
    )

    client = LknpdClient(LknpdConfig(inn="123", password="pw"), http=http)
    try:
        out1 = await client.issue_receipt(
            amount_rub=10.0,
            service_name="Voltari API",
            operation_time=datetime.now(UTC),
        )
        out2 = await client.issue_receipt(
            amount_rub=20.0,
            service_name="Voltari API",
            operation_time=datetime.now(UTC),
        )
    finally:
        await client.aclose()

    assert isinstance(out1, ReceiptIssued)
    assert out1.issuer == "lknpd"
    assert "rcpt_lknpd_1" in out1.receipt_url
    assert out2.receipt_id == "rcpt_lknpd_1"
    # Token cached → only one auth call.
    assert auth_calls == 1
    assert income_calls == 2


@pytest.mark.asyncio
async def test_lknpd_client_raises_on_auth_failure():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid creds"))
    http = httpx.AsyncClient(base_url="https://lknpd.nalog.ru/api/v1", transport=transport)
    client = LknpdClient(LknpdConfig(inn="x", password="y"), http=http)
    try:
        with pytest.raises(RuntimeError, match="lknpd_auth_failed"):
            await client.issue_receipt(
                amount_rub=1.0,
                service_name="x",
                operation_time=datetime.now(UTC),
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_lknpd_client_raises_on_income_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/lkfl"):
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200, json={"token": "t", "refreshToken": "rt", "tokenExpireIn": future}
            )
        return httpx.Response(500, text="ФНС лежит")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://lknpd.nalog.ru/api/v1", transport=transport)
    client = LknpdClient(LknpdConfig(inn="x", password="y"), http=http)
    try:
        with pytest.raises(RuntimeError, match="lknpd_income_failed"):
            await client.issue_receipt(
                amount_rub=10.0,
                service_name="Voltari",
                operation_time=datetime.now(UTC),
            )
    finally:
        await client.aclose()


# ---------- issue_payg_receipt ---------------------------------------------


@pytest.mark.asyncio
async def test_issue_payg_receipt_prefers_yookassa_when_available():
    """ЮKassa returns a ready receipt → lknpd untouched."""
    yk_transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"items": [{"id": "rcpt_yk_1", "receipt_url": "u"}]})
    )
    yk_http = httpx.AsyncClient(base_url="https://yk.test/v3", transport=yk_transport)

    out = await issue_payg_receipt(
        yookassa_http=yk_http,
        lknpd=None,  # not configured — but we shouldn't even need it
        payment_id="pay_1",
        amount_kopecks=10_000,
    )
    await yk_http.aclose()
    assert isinstance(out, ReceiptIssued)
    assert out.issuer == "yookassa"


@pytest.mark.asyncio
async def test_issue_payg_receipt_falls_back_to_lknpd():
    """ЮKassa fails (404) → lknpd path engages and issues the receipt."""
    yk_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))
    yk_http = httpx.AsyncClient(base_url="https://yk.test/v3", transport=yk_transport)

    def lknpd_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/lkfl"):
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200, json={"token": "t", "refreshToken": "rt", "tokenExpireIn": future}
            )
        return httpx.Response(200, json={"approvedReceiptUuid": "rcpt_npd"})

    lknpd_http = httpx.AsyncClient(
        base_url="https://lknpd.nalog.ru/api/v1",
        transport=httpx.MockTransport(lknpd_handler),
    )
    lknpd = LknpdClient(LknpdConfig(inn="x", password="y"), http=lknpd_http)

    out = await issue_payg_receipt(
        yookassa_http=yk_http,
        lknpd=lknpd,
        payment_id="pay_1",
        amount_kopecks=10_000,
    )
    await yk_http.aclose()
    await lknpd.aclose()
    assert isinstance(out, ReceiptIssued)
    assert out.issuer == "lknpd"


@pytest.mark.asyncio
async def test_issue_payg_receipt_returns_error_when_lknpd_fails():
    yk_transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))
    yk_http = httpx.AsyncClient(base_url="https://yk.test/v3", transport=yk_transport)

    def lknpd_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/lkfl"):
            return httpx.Response(401, text="bad creds")
        return httpx.Response(404)

    lknpd_http = httpx.AsyncClient(
        base_url="https://lknpd.nalog.ru/api/v1",
        transport=httpx.MockTransport(lknpd_handler),
    )
    lknpd = LknpdClient(LknpdConfig(inn="x", password="y"), http=lknpd_http)

    out = await issue_payg_receipt(
        yookassa_http=yk_http,
        lknpd=lknpd,
        payment_id="pay_1",
        amount_kopecks=10_000,
    )
    await yk_http.aclose()
    await lknpd.aclose()
    assert isinstance(out, ReceiptError)
    assert out.issuer_attempted == "lknpd"
    assert out.retryable is True


@pytest.mark.asyncio
async def test_issue_payg_receipt_returns_error_when_no_issuer_configured():
    out = await issue_payg_receipt(
        yookassa_http=None,
        lknpd=None,
        payment_id="pay_orphan",
        amount_kopecks=5_000,
    )
    assert isinstance(out, ReceiptError)
    assert "no_receipt_issuer_configured" in out.reason


@pytest.mark.asyncio
async def test_issue_payg_receipt_swallows_yookassa_exception_then_falls_back():
    """If the ЮKassa polling raises (e.g. transient network), we should still
    try lknpd rather than propagate the error to the webhook handler.
    """

    def boom(req):
        raise RuntimeError("yk lit on fire")

    yk_http = httpx.AsyncClient(base_url="https://yk.test/v3", transport=httpx.MockTransport(boom))

    def lknpd_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/lkfl"):
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200, json={"token": "t", "refreshToken": "rt", "tokenExpireIn": future}
            )
        return httpx.Response(200, json={"approvedReceiptUuid": "rcpt_after_yk_fail"})

    lknpd_http = httpx.AsyncClient(
        base_url="https://lknpd.nalog.ru/api/v1",
        transport=httpx.MockTransport(lknpd_handler),
    )
    lknpd = LknpdClient(LknpdConfig(inn="x", password="y"), http=lknpd_http)

    out = await issue_payg_receipt(
        yookassa_http=yk_http,
        lknpd=lknpd,
        payment_id="pay_1",
        amount_kopecks=1_000,
    )
    await yk_http.aclose()
    await lknpd.aclose()
    assert isinstance(out, ReceiptIssued)
    assert out.issuer == "lknpd"
