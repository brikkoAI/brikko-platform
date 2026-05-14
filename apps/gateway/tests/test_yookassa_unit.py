"""Unit tests for the YooKassa client/parser without HTTP or DB.

Focused on the changes from 2026-05-08 (СБП + T-Pay support):

1. ``create_payment`` injects ``payment_method_data.type`` only when the
   caller pre-selects a method; default = ЮKassa picker (no field).
2. ``create_payment`` rejects unknown method names before talking to ЮKassa.
3. ``parse_webhook`` extracts the method type into ``payment_method_type``
   so we can do "which method is most popular?" analytics.
"""

from __future__ import annotations

import json
import uuid

import pytest
import respx

from voltari_gateway.billing.yookassa import (
    ALLOWED_PAYMENT_METHODS,
    YooKassaClient,
    YooKassaConfig,
    YooKassaError,
    parse_webhook,
)


def _config() -> YooKassaConfig:
    return YooKassaConfig(
        shop_id="1345959",
        secret_key="test-secret",
        webhook_secret="whk-secret",
        return_url_template="https://brikko.ru/billing/return?account={account_id}",
        base_url="https://api.yookassa.test/v3",
    )


@pytest.fixture
def client():
    return YooKassaClient(config=_config())


# ---------- create_payment ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(ALLOWED_PAYMENT_METHODS))
async def test_create_payment_routes_chosen_method(client, method):
    """When caller pre-selects, payload carries payment_method_data.type."""
    yk_payload = {
        "id": "pay_x",
        "status": "pending",
        "amount": {"value": "100.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "confirmation_url": "https://yk/x"},
    }
    with respx.mock(base_url="https://api.yookassa.test/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        await client.create_payment(
            account_id=uuid.uuid4(),
            amount_kopecks=10_000,
            description="test",
            payment_method=method,
        )
    sent = json.loads(route.calls[0].request.content)
    assert sent["payment_method_data"] == {"type": method}


@pytest.mark.asyncio
async def test_create_payment_default_omits_method_data(client):
    """Default — no payment_method_data, ЮKassa picks for the user."""
    yk_payload = {
        "id": "pay_x",
        "status": "pending",
        "amount": {"value": "100.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "confirmation_url": "https://yk/x"},
    }
    with respx.mock(base_url="https://api.yookassa.test/v3") as mock:
        route = mock.post("/payments").respond(200, json=yk_payload)
        await client.create_payment(
            account_id=uuid.uuid4(),
            amount_kopecks=10_000,
            description="test",
        )
    sent = json.loads(route.calls[0].request.content)
    assert "payment_method_data" not in sent


@pytest.mark.asyncio
async def test_create_payment_rejects_unknown_method(client):
    """Bad method name fails before any HTTP call."""
    with pytest.raises(YooKassaError, match="unsupported payment_method"):
        await client.create_payment(
            account_id=uuid.uuid4(),
            amount_kopecks=10_000,
            description="test",
            payment_method="paypal",  # type: ignore[arg-type]
        )


# ---------- parse_webhook -----------------------------------------------------


def _webhook_body(method_type: str | None, method_id: str | None = "pm_123") -> bytes:
    obj = {
        "id": "pay_w_1",
        "status": "succeeded",
        "amount": {"value": "500.00", "currency": "RUB"},
        "metadata": {},
    }
    if method_type or method_id:
        obj["payment_method"] = {}
        if method_type:
            obj["payment_method"]["type"] = method_type
        if method_id:
            obj["payment_method"]["id"] = method_id
    return json.dumps(
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": obj,
        }
    ).encode("utf-8")


@pytest.mark.parametrize("method_type", ["bank_card", "sbp", "tinkoff_bank", "sberbank"])
def test_parse_webhook_extracts_method_type(method_type):
    """Method type from ЮKassa payload bubbles up into WebhookResult."""
    result = parse_webhook(_webhook_body(method_type=method_type))
    assert result.payment_method_type == method_type
    assert result.payment_method_id == "pm_123"


def test_parse_webhook_handles_missing_method_block():
    """Refund/cancel webhooks may not include payment_method — None is fine."""
    result = parse_webhook(_webhook_body(method_type=None, method_id=None))
    assert result.payment_method_type is None
    assert result.payment_method_id is None


def test_parse_webhook_method_id_only():
    """If id is present but type isn't (older webhook shape), don't crash."""
    result = parse_webhook(_webhook_body(method_type=None, method_id="pm_x"))
    assert result.payment_method_id == "pm_x"
    assert result.payment_method_type is None
