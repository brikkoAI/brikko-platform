"""Самозанятый-режим: автоматический выпуск чеков НПД.

POC verdict (2026-04-29 — backend-dev):

* **ЮKassa "Самозанятые"** (https://yookassa.ru/developers/payments/payment-process/self-employed)
  существует, но это **отдельный продукт** ЮKassa, оформляется отдельным
  договором с шопом-самозанятым. Он автоматически выпускает чеки НПД через
  API ФНС, **если** в payload `create_payment` передан блок ``receipt`` с
  ``vat_code: 1`` и контактом плательщика. Что мы и делаем в
  ``yookassa.create_payment``.
  Технически работает: чек прилетает на email/SMS клиенту, в нашей БД
  доступен через ``GET /payments/{id}/receipts``. Этого **достаточно** для
  PAYG-флоу при условии, что мы (или CEO) подключены к ЮKassa-самозанятые.

* **Fallback: lknpd.nalog.ru** ("Мой налог" API) — публичный API ФНС
  (https://lknpd.nalog.ru/api/v1/auth/lkfl, /api/v1/income, /api/v1/receipt).
  Авторизация по логину+паролю кабинета НПД, токен живёт 24 часа.
  Используем если ЮKassa-самозанятые недоступен (например в первые дни,
  пока договор оформляется) ИЛИ если ЮKassa-API вернул ошибку при выпуске.

**Открытые вопросы CEO/legal** — см. ``OPEN_QUESTIONS.md``:
1. Заведён ли счёт «ЮKassa Самозанятые» в личном кабинете? (если нет —
   PAYG-чеки до подключения едут через fallback).
2. После M9 (переход на ОСН/УСН) этот модуль становится off — чеки идут
   через ОФД (ЮKassa-онлайн-кассы или Atol). Заложен флаг `enable_npd`.
3. Лимит самозанятого 2.4 млн ₽/год — у нас отдельный cron-monitor должен
   считать выручку и предупреждать за 200k до лимита (не делается здесь;
   tracking — в админке).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ReceiptIssued:
    """Result of a successful receipt issuance."""

    receipt_id: str
    receipt_url: str  # ЮKassa или lknpd; в обоих случаях публичный URL
    issuer: str  # "yookassa" | "lknpd"
    issued_at: datetime


@dataclass(frozen=True)
class ReceiptError:
    """Receipt could not be issued; caller should retry / alert ops."""

    reason: str
    issuer_attempted: str
    retryable: bool


# ---------- ЮKassa-self-employed ---------------------------------------------


async def fetch_yookassa_receipt(http: httpx.AsyncClient, payment_id: str) -> ReceiptIssued | None:
    """GET /v3/receipts?payment_id=… — найти автоматически выпущенный чек.

    Возвращает None если чек ещё не выпустили (обычно занимает 5-30 сек после
    payment.succeeded). Caller-у предстоит ретраить.
    """
    try:
        resp = await http.get("/receipts", params={"payment_id": payment_id})
    except httpx.HTTPError as exc:
        log.warning("yookassa_receipt_fetch_failed", payment_id=payment_id, error=str(exc))
        return None

    if resp.status_code >= 400:
        log.warning(
            "yookassa_receipt_status",
            payment_id=payment_id,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None

    data = resp.json()
    items = data.get("items") or []
    if not items:
        return None
    receipt = items[0]
    return ReceiptIssued(
        receipt_id=receipt["id"],
        receipt_url=receipt.get("receipt_url") or "",
        issuer="yookassa",
        issued_at=datetime.now(UTC),
    )


async def wait_for_yookassa_receipt(
    http: httpx.AsyncClient,
    payment_id: str,
    *,
    max_wait_seconds: float = 30.0,
    poll_interval_seconds: float = 2.5,
) -> ReceiptIssued | None:
    """Poll ЮKassa /receipts until the auto-issued cheque appears or timeout."""
    elapsed = 0.0
    while elapsed < max_wait_seconds:
        receipt = await fetch_yookassa_receipt(http, payment_id)
        if receipt is not None:
            return receipt
        await asyncio.sleep(poll_interval_seconds)
        elapsed += poll_interval_seconds
    return None


# ---------- lknpd.nalog.ru fallback -------------------------------------------


@dataclass(frozen=True)
class LknpdConfig:
    """Credentials for «Мой налог» API.

    Auth flow: POST /api/v1/auth/lkfl with {username, password, deviceInfo}
    → returns {token, refreshToken, tokenExpireIn}. Token is reused until
    expiry (~24h).
    """

    inn: str
    password: str
    base_url: str = "https://lknpd.nalog.ru/api/v1"
    timeout_seconds: float = 30.0


class LknpdClient:
    """Минимальный клиент к «Мой налог» — auth + создание чека.

    Не production-grade Russian-tax-stack — это *fallback*. Если он
    становится основным выпуском чеков — переписать с retry/refresh-token,
    нормальной обработкой 4xx, и кэшированием токена в Redis (а не в памяти
    процесса).
    """

    def __init__(self, config: LknpdConfig, http: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )
        self._token: str | None = None
        self._token_exp: datetime | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self) -> str:
        if self._token and self._token_exp and self._token_exp > datetime.now(UTC):
            return self._token

        resp = await self._http.post(
            "/auth/lkfl",
            json={
                "username": self._config.inn,
                "password": self._config.password,
                "deviceInfo": {
                    "appVersion": "1.0.0",
                    "sourceType": "WEB",
                    "sourceDeviceId": "voltari-gateway",
                    "metaDetails": {"userAgent": "voltari-gateway/1.0"},
                },
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"lknpd_auth_failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._token = data["token"]
        # tokenExpireIn — ISO timestamp
        self._token_exp = datetime.fromisoformat(data["tokenExpireIn"].replace("Z", "+00:00"))
        return self._token

    async def issue_receipt(
        self,
        *,
        amount_rub: float,
        service_name: str,
        operation_time: datetime,
        client_inn: str | None = None,
        client_display_name: str | None = None,
    ) -> ReceiptIssued:
        """POST /api/v1/income — выпустить чек НПД."""
        token = await self._ensure_token()
        body = {
            "operationTime": operation_time.isoformat(),
            "requestTime": datetime.now(UTC).isoformat(),
            "services": [
                {
                    "name": service_name[:128],
                    "amount": round(amount_rub, 2),
                    "quantity": 1,
                }
            ],
            "totalAmount": round(amount_rub, 2),
            "client": (
                {
                    "contactPhone": None,
                    "displayName": client_display_name,
                    "inn": client_inn,
                    "incomeType": "FROM_LEGAL_ENTITY" if client_inn else "FROM_INDIVIDUAL",
                }
                if client_display_name
                else {
                    "contactPhone": None,
                    "displayName": None,
                    "inn": None,
                    "incomeType": "FROM_INDIVIDUAL",
                }
            ),
            "paymentType": "CASH",
            "ignoreMaxTotalIncomeRestriction": False,
        }
        resp = await self._http.post(
            "/income",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"lknpd_income_failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        receipt_id = data["approvedReceiptUuid"]
        receipt_url = f"{self._config.base_url.replace('/api/v1', '')}/receipt/{self._config.inn}/{receipt_id}/print"
        return ReceiptIssued(
            receipt_id=receipt_id,
            receipt_url=receipt_url,
            issuer="lknpd",
            issued_at=datetime.now(UTC),
        )


# ---------- orchestration -----------------------------------------------------


async def issue_payg_receipt(
    *,
    yookassa_http: httpx.AsyncClient | None,
    lknpd: LknpdClient | None,
    payment_id: str,
    amount_kopecks: int,
    service_name: str = "Услуги Voltari API",
    client_display_name: str | None = None,
    client_inn: str | None = None,
) -> ReceiptIssued | ReceiptError:
    """Try ЮKassa first, fall back to «Мой налог» on failure / unavailability.

    This is the function called from the webhook handler after a successful
    PAYG topup. Returns a typed result; the caller persists ``receipt_id`` /
    ``receipt_url`` against the matching ``Transaction`` row.
    """
    # 1) ЮKassa-самозанятые путь
    if yookassa_http is not None:
        try:
            receipt = await wait_for_yookassa_receipt(
                yookassa_http, payment_id, max_wait_seconds=15.0
            )
            if receipt is not None:
                return receipt
        except Exception as exc:
            log.warning("yookassa_receipt_path_failed", payment_id=payment_id, error=str(exc))

    # 2) lknpd fallback
    if lknpd is not None:
        try:
            return await lknpd.issue_receipt(
                amount_rub=amount_kopecks / 100.0,
                service_name=service_name,
                operation_time=datetime.now(UTC),
                client_display_name=client_display_name,
                client_inn=client_inn,
            )
        except Exception as exc:
            log.exception("lknpd_receipt_failed", payment_id=payment_id)
            return ReceiptError(
                reason=str(exc),
                issuer_attempted="lknpd",
                retryable=True,
            )

    return ReceiptError(
        reason="no_receipt_issuer_configured",
        issuer_attempted="none",
        retryable=False,
    )
