"""ЮKassa payments — create / webhook / recurring.

Wire format: `https://api.yookassa.ru/v3/payments` (Basic auth `shopId:secret`).
Idempotency on the create-side is via the ``Idempotence-Key`` header (ЮKassa
spec). Idempotency on the webhook-side is via ``transactions.ref_id`` —
``credit_account`` short-circuits a duplicate webhook.

Webhook signature: ЮKassa supports two flavours:

* HTTP Basic auth on the callback URL (cheapest, recommended for our scale).
* HMAC SHA1 of the raw body using ``IP+secret`` (legacy).

We implement HMAC SHA1 (matches the personal cabinet "Уведомления" tab) AND
fall back to Basic-Auth check if the merchant configured that instead. Both
are supported in the personal cabinet today (verified 2026-04-29).

References:
  * https://yookassa.ru/developers/api#create_payment
  * https://yookassa.ru/developers/using-api/webhooks
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

YOOKASSA_BASE_URL = "https://api.yookassa.ru/v3"
DEFAULT_TIMEOUT_SECONDS = 30.0

# ЮKassa payment_method_data.type values we accept from the API surface.
# `None` (default) lets ЮKassa show its own picker with every approved
# method on the merchant account. Pass an explicit value to skip the picker
# and open the chosen method directly — better UX from our own checkout.
#
# Mapping to the ЮKassa enum
# (https://yookassa.ru/developers/payment-acceptance/payment-methods):
#   "bank_card"    — банковские карты Visa / MC / Мир
#   "sbp"          — Система Быстрых Платежей (QR / SBPay)
#   "tinkoff_bank" — T-Pay (платёж через приложение Т-Банка)
#   "sberbank"     — SberPay (платёж через приложение Сбербанка), 2026-05-08
PaymentMethod = Literal["bank_card", "sbp", "tinkoff_bank", "sberbank"]
ALLOWED_PAYMENT_METHODS: tuple[PaymentMethod, ...] = (
    "bank_card",
    "sbp",
    "tinkoff_bank",
    "sberbank",
)


# ---------- config -------------------------------------------------------------


@dataclass(frozen=True)
class YooKassaConfig:
    shop_id: str
    secret_key: str
    webhook_secret: str  # for HMAC signature on incoming webhooks
    return_url_template: str  # e.g. "https://brikko.ru/billing/return?account={account_id}"
    base_url: str = YOOKASSA_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


# ---------- result types -------------------------------------------------------


@dataclass(frozen=True)
class PaymentURL:
    """What the API endpoint returns to the frontend after ``create_payment``."""

    payment_id: str
    confirmation_url: str
    amount_kopecks: int
    status: str  # "pending" right after creation


@dataclass(frozen=True)
class WebhookResult:
    """Parsed and validated webhook payload."""

    event: str  # "payment.succeeded" / "payment.canceled" / "refund.succeeded"
    payment_id: str
    amount_kopecks: int
    metadata: dict[str, Any]
    payment_method_id: str | None  # for save-card / recurring
    payment_method_type: str | None  # "bank_card" / "sbp" / "tinkoff_bank" — analytics
    raw: dict[str, Any]


@dataclass(frozen=True)
class Subscription:
    """Saved-card handle returned by ``setup_recurring``."""

    payment_method_id: str
    card_last4: str | None
    card_type: str | None


# ---------- errors -------------------------------------------------------------


class YooKassaError(Exception):
    """Generic YooKassa error — caller maps to upstream_error / 502."""


class YooKassaSignatureError(YooKassaError):
    """Webhook signature did not validate. Treat as authentication_error."""


# ---------- client -------------------------------------------------------------


class YooKassaClient:
    """Thin async wrapper around YooKassa's REST API.

    Stateless except for the underlying httpx client. Inject in app startup
    (see ``main.py`` lifespan).
    """

    def __init__(self, config: YooKassaConfig, http: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            auth=(config.shop_id, config.secret_key),
            headers={"Content-Type": "application/json"},
        )

    @property
    def config(self) -> YooKassaConfig:
        return self._config

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- payments ---

    async def create_payment(
        self,
        *,
        account_id: uuid.UUID,
        amount_kopecks: int,
        description: str,
        return_url: str | None = None,
        save_payment_method: bool = False,
        receipt_email: str | None = None,
        receipt_phone: str | None = None,
        metadata: dict[str, Any] | None = None,
        payment_method: PaymentMethod | None = None,
    ) -> PaymentURL:
        """Create a payment with redirect confirmation.

        Args:
          payment_method: when ``None`` (default) ЮKassa shows its own
            picker with every approved method on the shop. Pass one of
            ``ALLOWED_PAYMENT_METHODS`` to skip the picker and route the
            user straight to the chosen method's confirmation flow.
            Improves UX when our own checkout already lets the user
            choose card / СБП / T-Pay.

        Idempotence-Key = sha256(account_id|amount|nonce). The nonce is a
        per-call uuid4 so each invocation is unique unless the caller passes
        a stable one in metadata.
        """
        if amount_kopecks <= 0:
            raise YooKassaError("amount_kopecks must be positive")
        if payment_method is not None and payment_method not in ALLOWED_PAYMENT_METHODS:
            raise YooKassaError(
                f"unsupported payment_method: {payment_method!r}. "
                f"Allowed: {ALLOWED_PAYMENT_METHODS}"
            )

        amount_rub = f"{amount_kopecks / 100:.2f}"
        idem_key = uuid.uuid4().hex
        body: dict[str, Any] = {
            "amount": {"value": amount_rub, "currency": "RUB"},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url
                or self._config.return_url_template.format(account_id=account_id),
            },
            "description": description[:128],  # YooKassa limit
            "metadata": {**(metadata or {}), "account_id": str(account_id)},
        }
        if save_payment_method:
            body["save_payment_method"] = True
        if payment_method is not None:
            # Pre-selecting a method lets ЮKassa skip its own picker page
            # and jump straight to the confirmation flow (e.g. SBP QR or
            # T-Pay deeplink). The merchant account must have the method
            # approved (verified for our shop on 2026-05-08).
            body["payment_method_data"] = {"type": payment_method}

        # Receipt for самозанятый (54-ФЗ + НПД). Required for PAYG topups so
        # the cheque is auto-issued by ЮKassa-самозанятые.
        if receipt_email or receipt_phone:
            customer: dict[str, Any] = {}
            if receipt_email:
                customer["email"] = receipt_email
            if receipt_phone:
                customer["phone"] = receipt_phone
            body["receipt"] = {
                "customer": customer,
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {"value": amount_rub, "currency": "RUB"},
                        "vat_code": 1,  # 1 = "без НДС" (НПД самозанятого)
                        "payment_subject": "service",
                        "payment_mode": "full_prepayment",
                    }
                ],
            }

        try:
            resp = await self._http.post(
                "/payments",
                json=body,
                headers={"Idempotence-Key": idem_key},
            )
        except httpx.HTTPError as exc:
            raise YooKassaError(f"yookassa_unreachable: {exc}") from exc

        if resp.status_code >= 400:
            log.warning(
                "yookassa_create_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise YooKassaError(f"yookassa_status_{resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        confirmation = data.get("confirmation") or {}
        return PaymentURL(
            payment_id=data["id"],
            confirmation_url=confirmation.get("confirmation_url", ""),
            amount_kopecks=round(float(data["amount"]["value"]) * 100),
            status=data["status"],
        )

    async def charge_recurring(
        self,
        *,
        account_id: uuid.UUID,
        amount_kopecks: int,
        payment_method_id: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentURL:
        """Charge a previously saved card. Used by autorefill."""
        if amount_kopecks <= 0:
            raise YooKassaError("amount_kopecks must be positive")

        amount_rub = f"{amount_kopecks / 100:.2f}"
        body = {
            "amount": {"value": amount_rub, "currency": "RUB"},
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": description[:128],
            "metadata": {**(metadata or {}), "account_id": str(account_id), "autorefill": "1"},
        }
        idem_key = uuid.uuid4().hex
        try:
            resp = await self._http.post(
                "/payments",
                json=body,
                headers={"Idempotence-Key": idem_key},
            )
        except httpx.HTTPError as exc:
            raise YooKassaError(f"yookassa_unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise YooKassaError(f"yookassa_recurring_status_{resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return PaymentURL(
            payment_id=data["id"],
            confirmation_url="",
            amount_kopecks=round(float(data["amount"]["value"]) * 100),
            status=data["status"],
        )

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        try:
            resp = await self._http.get(f"/payments/{payment_id}")
        except httpx.HTTPError as exc:
            raise YooKassaError(f"yookassa_unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise YooKassaError(f"yookassa_get_status_{resp.status_code}")
        body: dict[str, Any] = resp.json()
        return body


# ---------- webhook --------------------------------------------------------


def verify_webhook_signature(raw_body: bytes, header_signature: str | None, secret: str) -> bool:
    """Validate ЮKassa webhook HMAC.

    ЮKassa places the signature in the ``Content-HMAC`` header in the form
    ``sha1=<hex>`` (or ``sha256=...`` for newer accounts). We compute HMAC over
    the raw body. Constant-time compare.

    If the merchant configured a different signing scheme (Basic auth on the
    callback path), they should bypass this and configure nginx/Caddy to
    enforce auth before the request hits us.
    """
    if not header_signature or not secret:
        return False
    parts = header_signature.split("=", 1)
    if len(parts) != 2:
        return False
    algo, provided_hex = parts[0].lower(), parts[1].lower()

    digestmod = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}.get(algo)
    if digestmod is None:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, digestmod).hexdigest().lower()
    return hmac.compare_digest(expected, provided_hex)


def parse_webhook(raw_body: bytes) -> WebhookResult:
    """Parse a ЮKassa webhook envelope into a typed result.

    Envelope (v3):
        {"type":"notification", "event":"payment.succeeded",
         "object":{ id, status, amount:{value,currency}, metadata, payment_method:{id,...} }}
    """
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YooKassaError(f"webhook_invalid_json: {exc}") from exc

    event = data.get("event") or ""
    obj = data.get("object") or {}
    payment_id = obj.get("id") or ""
    if not payment_id:
        raise YooKassaError("webhook_missing_payment_id")
    amount_str = (obj.get("amount") or {}).get("value", "0")
    try:
        amount_kopecks = round(float(amount_str) * 100)
    except (TypeError, ValueError) as exc:
        raise YooKassaError(f"webhook_bad_amount: {amount_str!r}") from exc
    # Sprint 5 hardening: reject *negative* amounts at the parse boundary
    # (``credit_account`` would later raise, but a negative amount that
    # reaches event-dispatch has already been signed-and-attributed in
    # logs as if it were legitimate). Zero is allowed through so the
    # API-layer DLQ path can record it for ops review (existing
    # contract — see test_webhook_amount_zero_returns_500).
    if amount_kopecks < 0:
        raise YooKassaError(f"webhook_negative_amount: {amount_str!r}")

    payment_method_obj = obj.get("payment_method") or {}
    payment_method_id = payment_method_obj.get("id")
    payment_method_type = payment_method_obj.get("type")
    metadata = obj.get("metadata") or {}

    return WebhookResult(
        event=event,
        payment_id=payment_id,
        amount_kopecks=amount_kopecks,
        metadata=metadata,
        payment_method_id=payment_method_id,
        payment_method_type=payment_method_type,
        raw=data,
    )
