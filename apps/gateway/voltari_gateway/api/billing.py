"""Billing-side HTTP endpoints.

Routes mounted under ``/v1/billing``:

* ``GET    /balance``                  — current balance for the caller.
* ``GET    /transactions``             — paginated transaction history.
* ``GET    /receipts/{tx_id}``         — fetch the cheque URL/PDF stub.
* ``POST   /topup``                    — create a ЮKassa payment, return URL.
* ``POST   /yookassa/webhook``         — accept ЮKassa server-to-server.
* ``POST   /autorefill``               — enable / update autorefill.
* ``DELETE /autorefill``               — disable autorefill.

All client-facing routes accept **either** a Bearer API key (M2M / SDK)
**or** a cookie session (browser dashboard) via ``require_api_key_or_session``.
The webhook is authenticated by HMAC signature (``Content-HMAC`` header) and
*never* by either user-facing method — it's not the customer talking to us.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.middleware import Principal, require_api_key_or_session
from voltari_gateway.auth.session_middleware import SessionPrincipal, require_session
from voltari_gateway.billing.card_link import (
    CARD_LINK_PURPOSE,
    CARD_LINK_VERIFY_AMOUNT_KOPECKS,
    credit_card_link_welcome,
    has_card_already_linked,
    has_received_card_link_welcome,
)
from voltari_gateway.billing.documents import (
    AKT_MIN_AMOUNT_KOPECKS,
    UPD_MIN_AMOUNT_KOPECKS,
    CustomerInfo,
    GatewayInfo,
    render_akt_for_transaction,
    render_period_invoice,
    render_upd,
)
from voltari_gateway.billing.engine import (
    BillingError,
    credit_account,
    refund_account,
)
from voltari_gateway.billing.receipts import ReceiptIssued, issue_payg_receipt
from voltari_gateway.billing.subscription import (
    PAID_TIERS,
    activate_subscription,
    cancel_subscription,
    price_for_tier,
)
from voltari_gateway.billing.yookassa import (
    WebhookResult,
    YooKassaClient,
    YooKassaError,
    parse_webhook,
    verify_webhook_signature,
)
from voltari_gateway.db.models import (
    Account,
    ProcessedWebhook,
    Tariff,
    Transaction,
    TransactionKind,
    User,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import (
    GatewayError,
    authentication_error,
    invalid_request,
    upstream_error,
)
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing"])


# ---------- response models ---------------------------------------------------


class BalanceResponse(BaseModel):
    account_id: uuid.UUID
    balance_kopecks: int
    balance_rub: float
    tariff: str


class TransactionItem(BaseModel):
    id: uuid.UUID
    type: str
    amount_kopecks: int
    ref_id: str | None
    created_at: datetime
    meta: dict[str, Any] | None


class TransactionsResponse(BaseModel):
    items: list[TransactionItem]
    next_cursor: str | None = None
    # Sprint 8 F4 — pagination + filtering. ``total_count`` is the count
    # AFTER applied filters (so the SPA can render «Показано 50 из 124»).
    # ``limit`` / ``offset`` echo what the caller asked for, useful for
    # client-side cache key construction.
    total_count: int = 0
    limit: int = 50
    offset: int = 0


class TopupRequest(BaseModel):
    # Sprint 6, Блок 13 — minimum top-up bumped to 100 ₽ (was 1 ₽). Below
    # 100 ₽ ЮKassa fees eat the deposit; we don't want users discovering
    # this empirically. Exact tariff-specific minimums apply via
    # ``_MIN_TOPUP_KOPECKS_BY_TARIFF``.
    amount_rub: int = Field(ge=100, le=1_000_000)
    return_url: str | None = None
    save_payment_method: bool = False
    receipt_email: str | None = None
    receipt_phone: str | None = None
    # Optional pre-selected payment method (added 2026-05-08 after ЮKassa
    # approved card / SBP / T-Pay / SberPay for shop 1345959). When
    # omitted, ЮKassa shows its own picker on the confirmation page.
    # When set, the user is sent straight to the chosen method's flow.
    #   None           — show ЮKassa picker (legacy default)
    #   "bank_card"    — Visa / MC / Мир direct
    #   "sbp"          — Система Быстрых Платежей (QR / SBPay)
    #   "tinkoff_bank" — T-Pay deeplink for Т-Банк customers
    #   "sberbank"     — SberPay deeplink for Сбер customers (added later 2026-05-08)
    payment_method: Literal["bank_card", "sbp", "tinkoff_bank", "sberbank"] | None = None


class TopupResponse(BaseModel):
    payment_id: str
    confirmation_url: str
    amount_kopecks: int


class AutorefillRequest(BaseModel):
    payment_method_id: str = Field(min_length=8, max_length=128)
    threshold_kopecks: int = Field(ge=100, le=1_000_000_00)
    topup_kopecks: int = Field(ge=100, le=10_000_000_00)


# ---------- card link + subscription (Pivot 2026-05-15) ----------------------


class LinkCardRequest(BaseModel):
    """Initiate the card-link verification flow.

    The frontend POSTs this, gets a ``confirmation_url``, redirects the
    user to ЮKassa. ЮKassa captures 1 ₽ → calls our webhook → we save
    ``payment_method_id``, refund the 1 ₽, and credit 100 ₽ welcome.
    """

    return_url: str | None = Field(
        default=None,
        description=(
            "Where ЮKassa redirects the user after they complete the 1 ₽ "
            "verification. Falls back to a brikko.ru/app/billing default."
        ),
        max_length=512,
    )

    model_config = {"extra": "ignore"}


class LinkCardResponse(BaseModel):
    confirmation_url: str
    payment_id: str


class SubscribeRequest(BaseModel):
    tier: Literal["pro", "team"]

    model_config = {"extra": "ignore"}


class SubscribeResponse(BaseModel):
    """202 Accepted — the actual activation happens in the webhook handler."""

    status: Literal["pending"]
    payment_id: str
    tier: str
    amount_kopecks: int


class SubscriptionResponse(BaseModel):
    tier: str
    active_until: datetime | None
    canceled_at: datetime | None
    card_linked: bool
    card_last4: str | None


class ReceiptResponse(BaseModel):
    transaction_id: uuid.UUID
    receipt_id: str
    receipt_url: str
    issuer: Literal["yookassa", "lknpd"]


# ---------- per-tariff min-topup ----------------------------------------------


_MIN_TOPUP_KOPECKS_BY_TARIFF: dict[str, int] = {
    # Sprint 6, Блок 13: PAYG minimum lowered from 500 ₽ to 100 ₽
    # (CEO 30.04 — match the public landing copy).
    Tariff.PAYG.value: 100_00,
    Tariff.PRO.value: 2_000_00,
    Tariff.PRO_PRIVACY.value: 2_000_00,
    Tariff.TEAM.value: 2_000_00,
    Tariff.BUSINESS.value: 2_000_00,
    Tariff.BUSINESS_PLUS.value: 5_000_00,
}


# ---------- dependency helpers ------------------------------------------------


def get_yookassa(request: Request) -> YooKassaClient:
    """Pull the YooKassa client off app.state — wired in main.lifespan."""
    client: YooKassaClient | None = getattr(request.app.state, "yookassa", None)
    if client is None:
        raise GatewayError(
            status_code=503,
            message="Billing provider is not configured.",
            type="api_error",
            code="billing_unavailable",
        )
    return client


# ---------- balance / transactions / receipts ---------------------------------


@router.get(
    "/balance",
    response_model=BalanceResponse,
    tags=["billing"],
    summary="Get current account balance and tariff",
    description=(
        "Returns the live balance from Postgres (no caching), in both "
        "kopecks (canonical) and rubles (display). Auth: Bearer **or** "
        "session cookie."
    ),
)
async def get_balance(
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BalanceResponse:
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    return BalanceResponse(
        account_id=account.id,
        balance_kopecks=account.balance_kopecks,
        balance_rub=account.balance_kopecks / 100.0,
        tariff=account.tariff.value,
    )


@router.get(
    "/transactions",
    response_model=TransactionsResponse,
    tags=["billing"],
    summary="List recent transactions",
    description=(
        "Returns ledger rows (charges, top-ups, refunds, autorefills) "
        "sorted newest-first. Filters: ``from``/``to`` ISO-datetime, "
        "``kind`` (one of topup|charge|refund|subscription|autorefill), "
        "``min_amount``/``max_amount`` in kopecks (compared on absolute "
        "value), ``search`` (substring against ref_id and meta JSON). "
        "Pagination: ``limit`` (1..500, default 50), ``offset`` (default 0). "
        "Response includes ``total_count`` after filters for the SPA "
        "«N из M» counter. Date semantics: ``to`` is **inclusive** at "
        "the day granularity (TD-042)."
    ),
)
async def list_transactions(
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    kind: str | None = Query(
        default=None,
        description="Filter by transaction kind (topup|charge|refund|subscription|autorefill).",
    ),
    min_amount: int | None = Query(
        default=None,
        ge=0,
        description="Minimum |amount_kopecks| (absolute, kopecks).",
    ),
    max_amount: int | None = Query(
        default=None,
        ge=0,
        description="Maximum |amount_kopecks| (absolute, kopecks).",
    ),
    search: str | None = Query(
        default=None,
        max_length=128,
        description="Case-insensitive substring against ref_id and meta JSON.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TransactionsResponse:
    """List filtered transactions for the active account.

    Filtering strategy:

    * ``kind`` — exact match against the enum string. Unknown kinds raise
      400 (better than silently returning empty).
    * ``min_amount`` / ``max_amount`` — compared on **absolute** kopecks.
      A −500 ₽ charge has |amount|=500 ₽; the filter "purchases between
      100 ₽ and 1000 ₽" matches that row regardless of sign.
    * ``search`` — substring on ``ref_id`` and the meta JSON (cast to
      text). LIKE-based, case-insensitive. SQLite has no native JSONB
      operators so we cast meta to TEXT — slow for >100k rows, but
      fine at the customer's per-account scale (worst case ~few k
      transactions/account).

    Pagination is offset-based: simple, exact, paginates inclusive of
    new rows since last fetch (which is what the SPA expects). Cursor
    pagination would be more correct for an infinite-scroll feed, but
    the dashboard is page-by-page anyway.
    """
    base_filters = [Transaction.account_id == principal.account_id]
    if from_ts is not None:
        base_filters.append(Transaction.created_at >= from_ts)
    if to_ts is not None:
        base_filters.append(Transaction.created_at <= to_ts)
    if kind is not None:
        # Validate up front — Pydantic doesn't see the value as enum.
        valid_kinds = {k.value for k in TransactionKind}
        if kind not in valid_kinds:
            raise invalid_request(
                f"Unknown transaction kind {kind!r}; valid: {sorted(valid_kinds)}",
                param="kind",
                code="invalid_transaction_kind",
            )
        base_filters.append(Transaction.type == TransactionKind(kind))
    if min_amount is not None:
        # |amount| ≥ min_amount  →  amount ≥ min_amount OR amount ≤ -min_amount
        from sqlalchemy import or_

        base_filters.append(
            or_(
                Transaction.amount_kopecks >= min_amount,
                Transaction.amount_kopecks <= -min_amount,
            )
        )
    if max_amount is not None:
        # |amount| ≤ max_amount  →  -max_amount ≤ amount ≤ max_amount
        base_filters.append(Transaction.amount_kopecks <= max_amount)
        base_filters.append(Transaction.amount_kopecks >= -max_amount)
    if search:
        from sqlalchemy import cast, func, or_
        from sqlalchemy.types import String

        s = f"%{search.lower()}%"
        # ref_id is plain TEXT; meta needs cast on SQLite where JSON is TEXT,
        # and on PG it works through JSONB::text. ``func.lower`` keeps the
        # match case-insensitive on both.
        base_filters.append(
            or_(
                func.lower(func.coalesce(Transaction.ref_id, "")).like(s),
                func.lower(cast(Transaction.meta, String)).like(s),
            )
        )

    # --- count --------------------------------------------------------------
    from sqlalchemy import func

    count_stmt = select(func.count()).select_from(Transaction).where(*base_filters)
    total_count = int((await db.execute(count_stmt)).scalar_one())

    # --- page ---------------------------------------------------------------
    page_stmt = (
        select(Transaction)
        .where(*base_filters)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page_stmt)).scalars().all()
    return TransactionsResponse(
        items=[
            TransactionItem(
                id=row.id,
                type=row.type.value,
                amount_kopecks=row.amount_kopecks,
                ref_id=row.ref_id,
                created_at=row.created_at,
                meta=row.meta,
            )
            for row in rows
        ],
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get("/receipts/{transaction_id}", response_model=ReceiptResponse)
async def get_receipt(
    transaction_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReceiptResponse:
    tx = await db.get(Transaction, transaction_id)
    if tx is None or tx.account_id != principal.account_id:
        raise GatewayError(
            status_code=404,
            message="Transaction not found.",
            type="invalid_request_error",
            code="not_found",
        )
    meta = tx.meta or {}
    receipt = meta.get("receipt") or {}
    receipt_id = receipt.get("id")
    receipt_url = receipt.get("url")
    issuer = receipt.get("issuer")
    if not (receipt_id and receipt_url and issuer):
        raise GatewayError(
            status_code=404,
            message="No receipt available for this transaction yet.",
            type="invalid_request_error",
            code="receipt_pending",
        )
    return ReceiptResponse(
        transaction_id=tx.id,
        receipt_id=receipt_id,
        receipt_url=receipt_url,
        issuer=issuer,
    )


# ---------- topup -------------------------------------------------------------


@router.post(
    "/topup",
    response_model=TopupResponse,
    tags=["billing"],
    summary="Initiate a balance top-up via ЮKassa",
    description=(
        "Creates a ЮKassa payment and returns a redirect URL the SPA opens "
        "in a new tab. Money is **not** credited here — credit happens in "
        "the ``/yookassa/webhook`` callback after the user completes payment. "
        "Idempotency: same caller request body within 5 min returns the "
        "cached payment to avoid double-charging."
    ),
    responses={
        200: {"description": "Payment created; ``confirmation_url`` returned."},
        401: {"description": "Auth missing."},
        402: {"description": "Tariff doesn't allow top-up (e.g. fixed plan)."},
        502: {"description": "ЮKassa error during payment creation."},
    },
)
async def create_topup(
    body: TopupRequest,
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    yookassa: Annotated[YooKassaClient, Depends(get_yookassa)],
) -> TopupResponse:
    amount_kopecks = body.amount_rub * 100
    min_kop = _MIN_TOPUP_KOPECKS_BY_TARIFF.get(principal.tariff, 100)
    if amount_kopecks < min_kop:
        raise invalid_request(
            f"Minimum top-up for tariff {principal.tariff} is {min_kop // 100} ₽.",
            param="amount_rub",
            code="below_min_topup",
        )
    # PAYG top-ups need a contact for the самозанятый-чек (54-ФЗ + НПД).
    # Caller-supplied receipt_email / receipt_phone wins. Otherwise fall
    # back to the user's profile email — they signed up with one and that
    # is the canonical channel for receipts. We only fail loud when the
    # caller is on Bearer-key auth (no User record) AND didn't provide
    # an explicit contact — there's nothing to fall back to.
    receipt_email = body.receipt_email
    receipt_phone = body.receipt_phone
    if principal.tariff == Tariff.PAYG.value and not (receipt_email or receipt_phone):
        if principal.user and principal.user.email:
            receipt_email = principal.user.email
        else:
            raise invalid_request(
                "PAYG top-up requires receipt_email or receipt_phone for самозанятый-чек.",
                param="receipt_email",
                code="receipt_contact_required",
            )

    try:
        result = await yookassa.create_payment(
            account_id=principal.account_id,
            amount_kopecks=amount_kopecks,
            description=f"Brikko top-up {body.amount_rub} ₽",
            return_url=body.return_url,
            save_payment_method=body.save_payment_method,
            receipt_email=receipt_email,
            receipt_phone=receipt_phone,
            metadata={
                "account_id": str(principal.account_id),
                # Echo the chosen method into metadata for analytics
                # (which method our users actually pick from the picker).
                **({"payment_method": body.payment_method} if body.payment_method else {}),
            },
            payment_method=body.payment_method,
        )
    except YooKassaError as exc:
        log.warning("topup_yookassa_create_failed", error=str(exc))
        raise upstream_error("Could not create payment with the bank gateway.") from exc

    return TopupResponse(
        payment_id=result.payment_id,
        confirmation_url=result.confirmation_url,
        amount_kopecks=result.amount_kopecks,
    )


# ---------- ЮKassa webhook ---------------------------------------------------
#
# Status contract (BE P0-6):
#
#   401 Unauthorized   — HMAC signature invalid / missing. No DB writes.
#                        ЮKassa won't reach this in normal operation; this
#                        rejects forged callbacks.
#   400 Bad Request    — malformed body (not parseable JSON / no payment_id).
#                        ЮKassa won't retry 4xx — but a malformed payload
#                        from ЮKassa would be a platform bug, not transient.
#   500 Server Error   — recoverable failure during processing (BillingError,
#                        DB transient, account_id missing in metadata, etc.).
#                        ЮKassa retries 5xx aggressively — exactly what we
#                        want here. We also drop a row in `processed_webhooks`
#                        with status='failed' + error_message so an operator
#                        can find it after retries are exhausted.
#   200 OK             — processed (or already-processed: idempotent replay).
#                        Includes ignored events ('payment.canceled' etc.)
#                        because those are no-ops by design.


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _record_webhook_outcome(
    db: AsyncSession,
    *,
    payment_id: str,
    event: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Upsert into ``processed_webhooks`` with the given outcome.

    Behaviour:

    * If the row doesn't exist → INSERT with first_seen_at = processed_at = now.
    * If it exists and we're moving from ``failed`` → ``processed`` → UPDATE
      (a previous retry's transient failure has now succeeded).
    * If it exists and the new status is ``failed`` and the existing status
      is already ``processed`` → no-op (don't downgrade a happy path).

    The function commits (or rolls back) its own session — call it BEFORE
    or AFTER the main credit/refund commit, on a fresh session if the main
    one was rolled back.
    """
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    now = _utcnow()

    # Both Postgres and SQLite (3.24+) support ON CONFLICT DO UPDATE
    # via their dialect-specific ``insert(...)`` helper. The two helpers
    # don't share a base class but the surface we use is identical.
    insert_fn = pg_insert if is_postgres else sqlite_insert
    stmt = (
        insert_fn(ProcessedWebhook)
        .values(
            payment_id=payment_id,
            event=event,
            first_seen_at=now,
            processed_at=now,
            source="yookassa",
            status=status,
            error_message=error_message,
        )
        .on_conflict_do_update(
            index_elements=["payment_id"],
            set_={
                "processed_at": now,
                "status": status,
                "error_message": error_message,
                "event": event,
            },
            # Don't downgrade processed → failed (subsequent retries
            # observing a soft-failure shouldn't undo the success).
            where=ProcessedWebhook.status != "processed",
        )
    )
    await db.execute(stmt)
    await db.commit()


async def _is_already_processed(db: AsyncSession, payment_id: str) -> bool:
    """True iff we have a ``processed_webhooks`` row in status='processed'.

    Failed rows (status='failed') don't count — those are the DLQ entries
    we expect to retry. A new attempt on a 'failed' row is exactly the
    kind of retry ЮKassa is supposed to send.
    """
    row = await db.get(ProcessedWebhook, payment_id)
    return row is not None and row.status == "processed"


def _parse_account_id(parsed: WebhookResult) -> uuid.UUID | None:
    """Pull account_id from ``metadata`` and parse as UUID. None on miss."""
    metadata_account = parsed.metadata.get("account_id")
    if not metadata_account:
        return None
    try:
        return uuid.UUID(str(metadata_account))
    except (ValueError, TypeError):
        return None


def _server_error(detail: str, code: str = "webhook_processing_failed") -> JSONResponse:
    """500 response shaped like ``GatewayError`` for ЮKassa to retry."""
    return JSONResponse(
        {
            "error": {
                "type": "api_error",
                "code": code,
                "message": detail,
            }
        },
        status_code=500,
    )


@router.post("/yookassa/webhook")
async def yookassa_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    yookassa: Annotated[YooKassaClient, Depends(get_yookassa)],
    content_hmac: str | None = Header(default=None, alias="Content-HMAC"),
    query_secret: str | None = Query(default=None, alias="secret"),
) -> JSONResponse:
    """Accept ЮKassa server notifications.

    Returns 401 on bad signature, 400 on malformed body, 500 on
    recoverable processing failures (so ЮKassa retries), 200 on success
    or idempotent replay.

    Idempotency is enforced through both:
    * ``transactions.ref_id`` UNIQUE — so credit/refund cannot run twice.
    * ``processed_webhooks.payment_id`` PK with status='processed' — fast
      short-circuit for replays without taking the account row lock.
    """
    raw = await request.body()
    # Webhook auth schemes (ЮKassa lets the merchant pick):
    #   1. HMAC of the raw body in `Content-HMAC` header — verified below
    #      when ``YOOKASSA_WEBHOOK_SECRET`` is set in the env.
    #   2. HTTP Basic auth in the callback URL (Authorization header) —
    #      verified by Caddy upstream of this handler when present.
    #   3. None — the merchant relies on ЮKassa's fixed IP allowlist for
    #      origin authentication. Used during initial onboarding before
    #      a secret can be configured (ЮKassa's UI doesn't expose the
    #      HMAC secret field; it has to be set via API or grandfathered
    #      from a legacy account).
    #
    # webhook_secret обязателен. Без него любой POST к /v1/billing/webhook
    # с правильной полезной нагрузкой зачислил бы баланс — это прямой путь
    # утечки денег. До 2026-05-09 здесь был «bootstrap-fallback» с warning,
    # security-аудит ночного research'а пометил это как P0 (см.
    # 06_Operations/2026-05-09-night-research/10-security-threats-2026.md §1).
    #
    # `yookassa_webhook_signature_required` (default True) даёт временный
    # opt-out на момент bootstrap'а нового магазина в ЮKassa, пока секрет
    # не сгенерирован в ЛК и не положен в env. На production должен быть True.
    #
    # Какая аутентификация работает — зависит от того что доступно в ЛК ЮKassa
    # (по состоянию на 9 мая 2026 в их UI настраивается ТОЛЬКО URL — HMAC и
    # Basic Auth не предлагаются). Поэтому:
    #   1. Query-secret (`?secret=...` в URL HTTP-уведомлений) — главный метод.
    #   2. HMAC `Content-HMAC` header — fallback для legacy-магазинов.
    #   3. Bootstrap-mode (signature_required=False) — короткое окно после
    #      смены магазина, до прописки нового URL в ЛК.
    from voltari_gateway.config import get_settings as _get_settings  # local import

    settings = _get_settings()
    expected_secret = yookassa.config.webhook_secret

    if expected_secret:
        # Query-secret → константная сравнялка через hmac.compare_digest.
        if query_secret and hmac.compare_digest(query_secret, expected_secret):
            pass  # authenticated via URL query secret
        elif content_hmac and verify_webhook_signature(raw, content_hmac, expected_secret):
            pass  # authenticated via Content-HMAC header
        else:
            log.warning(
                "yookassa_webhook_bad_auth",
                has_query=bool(query_secret),
                has_hmac=bool(content_hmac),
            )
            raise authentication_error("Invalid webhook authentication.")
    else:
        # Нет секрета в env. Если signature_required=True — 503; иначе bootstrap.
        if settings.yookassa_webhook_signature_required:
            log.error(
                "yookassa_webhook_secret_missing",
                note="set YOOKASSA_WEBHOOK_SECRET in .env",
            )
            raise GatewayError(
                status_code=503,
                message="Webhook authentication not configured.",
                type="api_error",
                code="webhook_secret_missing",
            )
        log.warning(
            "yookassa_webhook_auth_skipped_bootstrap",
            note="set YOOKASSA_WEBHOOK_SECRET + YOOKASSA_WEBHOOK_SIGNATURE_REQUIRED=true",
        )

    try:
        parsed = parse_webhook(raw)
    except YooKassaError as exc:
        log.warning("yookassa_webhook_unparseable", error=str(exc))
        # 400 — malformed by definition; retrying won't fix it. ЮKassa will
        # eventually give up after their retry policy.
        raise invalid_request("Malformed webhook payload.", code="bad_webhook") from exc

    # --- Idempotency fast-path -----------------------------------------------
    # If we've already processed this payment_id successfully, return 200
    # without touching the account row (the row-lock is the slowest part).
    # A previously-failed entry falls through so we get another attempt.
    if await _is_already_processed(db, parsed.payment_id):
        log.info(
            "yookassa_webhook_replay_ok",
            payment_id=parsed.payment_id,
            yookassa_event=parsed.event,
        )
        return JSONResponse({"status": "ok", "replay": True}, status_code=200)

    # --- Validate metadata ---------------------------------------------------
    account_id = _parse_account_id(parsed)
    if account_id is None:
        # Missing or unparseable account_id is operator-level breakage —
        # a payment we can't attribute. Record in DLQ and tell ЮKassa to
        # retry: a config fix on our side might let us process it next time.
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message="missing_or_invalid_account_id",
        )
        log.error(
            "yookassa_webhook_no_account",
            payment_id=parsed.payment_id,
            metadata=dict(parsed.metadata),
        )
        return _server_error(
            "Webhook metadata missing valid account_id.",
            code="webhook_missing_account",
        )

    if parsed.amount_kopecks <= 0 and parsed.event != "refund.succeeded":
        # Zero / negative amount on a credit-side event is nonsensical.
        # Record + 500 so ops sees it.
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"invalid_amount_{parsed.amount_kopecks}",
        )
        log.error(
            "yookassa_webhook_bad_amount",
            payment_id=parsed.payment_id,
            amount=parsed.amount_kopecks,
        )
        return _server_error(
            f"Webhook amount_kopecks must be positive (got {parsed.amount_kopecks}).",
            code="webhook_invalid_amount",
        )

    # --- Dispatch by event ---------------------------------------------------
    if parsed.event == "payment.succeeded":
        return await _handle_payment_succeeded(request, db, account_id=account_id, parsed=parsed)

    if parsed.event == "refund.succeeded":
        return await _handle_refund_succeeded(db, account_id=account_id, parsed=parsed)

    # payment.canceled / payment.waiting_for_capture / unknown — no balance change.
    # Mark as processed so a duplicate delivery doesn't re-enter the dispatch.
    await _record_webhook_outcome(
        db,
        payment_id=parsed.payment_id,
        event=parsed.event,
        status="processed",
    )
    log.info(
        "yookassa_webhook_no_op",
        yookassa_event=parsed.event,
        payment_id=parsed.payment_id,
    )
    return JSONResponse({"status": "ok"}, status_code=200)


async def _handle_payment_succeeded(
    request: Request,
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    parsed: WebhookResult,
) -> JSONResponse:
    """Credit + receipt issuance for ``payment.succeeded`` events.

    Dispatches on ``metadata.purpose``:

      * ``"card_link_verification"`` — special flow: refund the 1 ₽ via
        ЮKassa, save ``payment_method.id``, credit 100 ₽ welcome. Does
        NOT add the 1 ₽ to balance (it's refunded).
      * ``"subscription_charge"`` — set ``subscription_tier`` + extend
        ``subscription_active_until`` by 30d. Does NOT credit the 290/1490
        to the balance (it's a subscription payment, not a topup).
      * default — legacy topup behaviour. Credits the full amount.

    Returns 500 (retryable) on any BillingError so ЮKassa retries; the
    DLQ row is written on a fresh session because the credit transaction
    has been rolled back.
    """
    purpose = parsed.metadata.get("purpose") if parsed.metadata else None

    if purpose == CARD_LINK_PURPOSE:
        return await _handle_card_link_succeeded(
            request, db, account_id=account_id, parsed=parsed
        )
    if purpose == "subscription_charge":
        return await _handle_subscription_succeeded(
            db, account_id=account_id, parsed=parsed
        )

    try:
        tx = await credit_account(
            db,
            account_id=account_id,
            amount_kopecks=parsed.amount_kopecks,
            ref_id=parsed.payment_id,
            kind=TransactionKind.TOPUP,
            meta={
                "source": "yookassa",
                "payment_method_id": parsed.payment_method_id,
                "raw_event": parsed.event,
            },
        )
        await db.commit()
    except IntegrityError:
        # Concurrent credit landed first (UNIQUE on (account_id, ref_id))
        # — idempotent path: reload the existing row and treat as success.
        await db.rollback()
        existing = (
            await db.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.ref_id == parsed.payment_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        tx = existing
    except BillingError as exc:
        # Recoverable — `credit_account` raises on account-not-found / DB
        # races. Roll back, record DLQ, return 500.
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"billing_error: {exc}"[:1024],
        )
        log.exception("yookassa_credit_failed", payment_id=parsed.payment_id)
        return _server_error(str(exc), code="webhook_credit_failed")

    # Mark as processed atomically with the credit. We intentionally do this
    # AFTER the credit commit so a webhook that crashed mid-receipt doesn't
    # leave us in a state where the account is credited but the DLQ row
    # says "failed".
    await _record_webhook_outcome(
        db,
        payment_id=parsed.payment_id,
        event=parsed.event,
        status="processed",
    )

    # Best-effort receipt issuance for PAYG. Failure here doesn't block —
    # the credit already landed. Only fetch the tariff column instead of
    # the whole Account row — we've already committed the credit txn and
    # don't need to mutate the account, just route the receipt path.
    tariff_value = (
        await db.execute(select(Account.tariff).where(Account.id == account_id))
    ).scalar_one_or_none()
    if tariff_value == Tariff.PAYG:
        yk_http = getattr(request.app.state, "yookassa_receipts_http", None)
        lknpd = getattr(request.app.state, "lknpd_client", None)
        res = await issue_payg_receipt(
            yookassa_http=yk_http,
            lknpd=lknpd,
            payment_id=parsed.payment_id,
            amount_kopecks=parsed.amount_kopecks,
        )
        if isinstance(res, ReceiptIssued):
            meta = dict(tx.meta or {})
            meta["receipt"] = {
                "id": res.receipt_id,
                "url": res.receipt_url,
                "issuer": res.issuer,
            }
            tx.meta = meta
            db.add(tx)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                log.warning("receipt_meta_persist_failed", payment_id=parsed.payment_id)
        else:
            log.warning(
                "payg_receipt_failed",
                payment_id=parsed.payment_id,
                reason=res.reason,
            )

    return JSONResponse({"status": "ok", "transaction_id": str(tx.id)}, status_code=200)


async def _handle_refund_succeeded(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    parsed: WebhookResult,
) -> JSONResponse:
    """Refund handler. Same DLQ semantics as payment_succeeded.

    Special case: refunds whose ``metadata.purpose == "card_link_verification"``
    are NOT debited from the balance — the 1 ₽ verification charge was never
    credited (the original ``payment.succeeded`` handler short-circuited
    into the card-link flow). We still mark the webhook processed so a
    replayed refund.succeeded doesn't re-enter dispatch.
    """
    purpose = parsed.metadata.get("purpose") if parsed.metadata else None
    if purpose == CARD_LINK_PURPOSE:
        log.info(
            "yookassa_refund_card_link_ack",
            payment_id=parsed.payment_id,
            account_id=str(account_id),
        )
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="processed",
        )
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        await refund_account(
            db,
            account_id=account_id,
            amount_kopecks=abs(parsed.amount_kopecks),
            ref_id=parsed.payment_id,  # refund id
            meta={"source": "yookassa", "raw_event": parsed.event},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent refund landed first — idempotent.
    except BillingError as exc:
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"billing_error: {exc}"[:1024],
        )
        log.exception("yookassa_refund_failed", payment_id=parsed.payment_id)
        return _server_error(str(exc), code="webhook_refund_failed")

    await _record_webhook_outcome(
        db,
        payment_id=parsed.payment_id,
        event=parsed.event,
        status="processed",
    )
    return JSONResponse({"status": "ok"}, status_code=200)


# ---------- card link + subscription webhook handlers ------------------------


async def _handle_card_link_succeeded(
    request: Request,
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    parsed: WebhookResult,
) -> JSONResponse:
    """``payment.succeeded`` for a 1 ₽ card-link verification charge.

    Steps:
      1. Refund the 1 ₽ via ЮKassa REST. Best-effort — if the refund fails
         we still save the pm_id and credit welcome (user is out 1 ₽; ops
         can manually refund). Logged with ``card_link_refund_failed``.
      2. Save ``payment_method.id`` to ``account.autorefill_pm_id``.
      3. Credit the 100 ₽ welcome bonus (idempotent on ref_id).
      4. Mark the webhook processed.

    All three side effects land in one DB transaction. If any fails, we
    rollback and ask ЮKassa to retry — autorefill_pm_id is empty so a
    subsequent attempt is fine.
    """
    yk: YooKassaClient = request.app.state.yookassa

    # 1) Refund the 1 ₽. Use our internal ref_id so a replayed webhook
    #    that already triggered a refund doesn't double-call ЮKassa
    #    (their /refunds endpoint is idempotent on Idempotence-Key, but
    #    we'd still burn a network round-trip).
    try:
        await yk.refund_payment(
            payment_id=parsed.payment_id,
            amount_kopecks=parsed.amount_kopecks,
            description="Brikko card-link verify refund",
            metadata={
                "purpose": CARD_LINK_PURPOSE,
                "account_id": str(account_id),
                "original_payment_id": parsed.payment_id,
            },
        )
    except YooKassaError as exc:
        # Soft-fail: log + continue. The pm_id save is still valuable.
        # Ops can manually refund the 1 ₽ if it doesn't auto-clear.
        log.warning(
            "card_link_refund_failed",
            payment_id=parsed.payment_id,
            account_id=str(account_id),
            error=str(exc),
        )

    # 2-3) Save pm_id + credit welcome.
    if not parsed.payment_method_id:
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message="card_link_missing_payment_method_id",
        )
        log.error(
            "card_link_no_pm_id",
            payment_id=parsed.payment_id,
            account_id=str(account_id),
        )
        return _server_error(
            "Card-link webhook missing payment_method.id",
            code="card_link_no_pm_id",
        )

    try:
        granted, new_balance = await credit_card_link_welcome(
            db,
            account_id=account_id,
            payment_method_id=parsed.payment_method_id,
        )
        await db.commit()
    except Exception as exc:  # DLQ + retry — catch-broad is intentional
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"card_link_credit_failed: {exc}"[:1024],
        )
        log.exception(
            "card_link_credit_failed",
            payment_id=parsed.payment_id,
            account_id=str(account_id),
        )
        return _server_error(str(exc), code="card_link_credit_failed")

    await _record_webhook_outcome(
        db,
        payment_id=parsed.payment_id,
        event=parsed.event,
        status="processed",
    )
    log.info(
        "card_link_succeeded",
        payment_id=parsed.payment_id,
        account_id=str(account_id),
        welcome_granted=granted,
        new_balance_kopecks=new_balance,
    )
    return JSONResponse(
        {
            "status": "ok",
            "welcome_granted": granted,
        },
        status_code=200,
    )


async def _handle_subscription_succeeded(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    parsed: WebhookResult,
) -> JSONResponse:
    """``payment.succeeded`` for a Pro/Team subscription charge.

    Activates the tier (+30d active_until). Does NOT credit the 290/1490
    to the cash balance — subscription is an entitlement, not a topup.
    """
    tier = parsed.metadata.get("tier") if parsed.metadata else None
    if tier not in PAID_TIERS:
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"subscription_bad_tier: {tier!r}",
        )
        log.error(
            "subscription_webhook_bad_tier",
            payment_id=parsed.payment_id,
            tier=tier,
        )
        return _server_error(
            f"Subscription webhook metadata has bad tier {tier!r}",
            code="subscription_bad_tier",
        )

    try:
        activated = await activate_subscription(
            db,
            account_id=account_id,
            tier=tier,
            payment_id=parsed.payment_id,
        )
        await db.commit()
    except Exception as exc:  # DLQ + retry — catch-broad is intentional
        await db.rollback()
        await _record_webhook_outcome(
            db,
            payment_id=parsed.payment_id,
            event=parsed.event,
            status="failed",
            error_message=f"subscription_activate_failed: {exc}"[:1024],
        )
        log.exception(
            "subscription_activate_failed",
            payment_id=parsed.payment_id,
            account_id=str(account_id),
        )
        return _server_error(str(exc), code="subscription_activate_failed")

    await _record_webhook_outcome(
        db,
        payment_id=parsed.payment_id,
        event=parsed.event,
        status="processed",
    )
    log.info(
        "subscription_webhook_succeeded",
        payment_id=parsed.payment_id,
        account_id=str(account_id),
        tier=tier,
        activated=activated,
    )
    return JSONResponse({"status": "ok", "activated": activated, "tier": tier}, status_code=200)


# ---------- autorefill --------------------------------------------------------
#
# Sprint 8 F1 — alongside the existing POST/DELETE we now expose a GET so
# the SPA can render the current state without a roundtrip through the
# whole account snapshot. All three endpoints write an audit_log row on
# state change so the activity feed surfaces a "включено auto-refill"
# event next to balance / tariff changes.
#
# Schema for autorefill columns lived in Alembic 0002 already — Sprint 6
# added the saved-card pm_id and Sprint 7 wired the cron loop. There's
# no schema change needed in Sprint 8.


class AutorefillStatus(BaseModel):
    """Read-side schema for ``GET /v1/billing/autorefill``.

    ``payment_method_id`` is the ЮKassa saved-card handle. Returning it
    plain is fine — it isn't a card number, just an opaque ЮKassa ID
    that's only useful when paired with our shop credentials. The SPA
    needs it to render «карта •• 4242 — отвязать».
    """

    enabled: bool
    threshold_kopecks: int | None
    topup_kopecks: int | None
    payment_method_id: str | None


@router.get(
    "/autorefill",
    response_model=AutorefillStatus,
    summary="Read current autorefill settings",
    description=(
        "Returns the four autorefill columns as a single JSON. ``enabled`` "
        "is the master switch; threshold/topup are NULL when the account "
        "hasn't customised them (per-tariff defaults apply on the cron "
        "side — see ``billing.autorefill._settings_from_account``). "
        "``payment_method_id`` is the opaque ЮKassa saved-card handle."
    ),
)
async def get_autorefill(
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AutorefillStatus:
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    return AutorefillStatus(
        enabled=bool(account.autorefill_enabled),
        threshold_kopecks=account.autorefill_threshold_kopecks,
        topup_kopecks=account.autorefill_topup_kopecks,
        payment_method_id=account.autorefill_pm_id,
    )


@router.post("/autorefill", status_code=200)
async def enable_autorefill(
    body: AutorefillRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    if body.threshold_kopecks >= body.topup_kopecks:
        raise invalid_request(
            "topup_kopecks must be greater than threshold_kopecks.",
            param="topup_kopecks",
            code="autorefill_threshold_invalid",
        )

    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")

    was_enabled = bool(account.autorefill_enabled)
    account.autorefill_enabled = True
    account.autorefill_pm_id = body.payment_method_id
    account.autorefill_threshold_kopecks = body.threshold_kopecks
    account.autorefill_topup_kopecks = body.topup_kopecks
    db.add(account)

    # Audit. Single action name; ``meta.was_enabled`` lets the activity
    # feed distinguish "first-time enable" vs. "re-configured".
    await write_audit(
        db,
        user_id=principal.user_id,
        account_id=account.id,
        action="autorefill_enabled" if not was_enabled else "autorefill_updated",
        request=request,
        meta={
            "threshold_kopecks": body.threshold_kopecks,
            "topup_kopecks": body.topup_kopecks,
            "had_payment_method": account.autorefill_pm_id is not None,
        },
    )
    await db.commit()

    return {
        "enabled": True,
        "threshold_kopecks": body.threshold_kopecks,
        "topup_kopecks": body.topup_kopecks,
    }


@router.delete("/autorefill", status_code=200)
async def disable_autorefill(
    request: Request,
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    was_enabled = bool(account.autorefill_enabled)
    account.autorefill_enabled = False
    account.autorefill_pm_id = None
    db.add(account)
    if was_enabled:
        # Don't pollute the audit feed with no-op disables (SPA could
        # call DELETE on an already-disabled account out of paranoia).
        await write_audit(
            db,
            user_id=principal.user_id,
            account_id=account.id,
            action="autorefill_disabled",
            request=request,
            meta={"by": "user_request"},
        )
    await db.commit()
    return {"enabled": False}


# ---------- Comply Pack — auto-generated РФ documents -----------------------
#
# Sprint 4 Поток M. Three flavours: per-transaction АКТ, period invoice,
# and УПД. All return ``application/pdf`` with ``Content-Disposition:
# attachment``. fpdf2-rendered, A4, single page, Cyrillic-capable.
#
# Customer side reads from User+Account; gateway side from settings.brand_*.
# Missing fields render as ``<укажите …>`` placeholders so a half-finished
# customer profile still produces a usable draft document.


async def _build_customer_info(db: AsyncSession, account: Account) -> CustomerInfo:
    """Project an Account + its owner into CustomerInfo for rendering."""
    user = await db.get(User, account.owner_id)
    full_name = account.name or (user.email if user is not None else "<укажите наименование>")
    settings = (account.settings or {}).get("legal", {}) or {}
    return CustomerInfo(
        full_name=str(full_name),
        inn=str(settings.get("inn")) if settings.get("inn") else None,
        kpp=str(settings.get("kpp")) if settings.get("kpp") else None,
        address=str(settings.get("address")) if settings.get("address") else None,
        email=user.email if user is not None else None,
    )


def _gateway_info() -> GatewayInfo:
    """Voltari side from config. TD-001 will fill these once ИП is registered."""
    from voltari_gateway.config import get_settings as _gs

    s = _gs()
    return GatewayInfo(
        brand_name=s.brand_name,
    )


@router.get(
    "/documents/{transaction_id}/akt",
    summary="PDF: акт за конкретную транзакцию",
    description=(
        "Generates an «Акт выполненных работ» PDF for one transaction. "
        "Renders header (brand + customer), one-line goods table, signature "
        "block. Uses fpdf2 with Cyrillic-capable DejaVu Sans (falls back to "
        "Helvetica if the font isn't installed — Cyrillic will render as "
        "boxes but the PDF still issues)."
    ),
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF document"},
        404: {"description": "Transaction not found / does not belong to caller."},
    },
)
async def get_transaction_akt(
    transaction_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    tx = await db.get(Transaction, transaction_id)
    if tx is None or tx.account_id != principal.account_id:
        raise GatewayError(
            status_code=404,
            message="Transaction not found.",
            type="invalid_request_error",
            code="not_found",
        )
    # Sprint 8 — legal threshold gate. Sub-1000₽ transactions are not
    # eligible for акт (sub-1000 line-items are accounting line-noise,
    # bookkeepers reject them). Refunds are signed-negative — compare on
    # absolute value so a -500₽ refund is correctly flagged ineligible.
    if abs(tx.amount_kopecks) < AKT_MIN_AMOUNT_KOPECKS:
        raise invalid_request(
            (
                f"Transactions below {AKT_MIN_AMOUNT_KOPECKS // 100} ₽ are not "
                "eligible for акт; please use the period-invoice endpoint instead."
            ),
            param="transaction_id",
            code="amount_below_akt_threshold",
        )
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    customer = await _build_customer_info(db, account)
    pdf_bytes = render_akt_for_transaction(
        transaction=tx,
        customer=customer,
        gateway=_gateway_info(),
    )
    log.info(
        "akt_issued",
        account_id=str(principal.account_id),
        transaction_id=str(tx.id),
        size_bytes=len(pdf_bytes),
    )
    filename = f"voltari-akt-{str(tx.id)[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/documents/period",
    summary="PDF: сводный счёт за период",
    description=(
        "Generates a period invoice for ``[from, to]``. Empty period "
        "issues a doc with «нет операций» note rather than 404 — accountants "
        "need a paper trail of inactive months too."
    ),
    responses={200: {"content": {"application/pdf": {}}, "description": "PDF document"}},
)
async def get_period_invoice(
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    from_ts: datetime = Query(alias="from"),
    to_ts: datetime = Query(alias="to"),
) -> Response:
    if to_ts < from_ts:
        raise invalid_request("`to` must be >= `from`.", param="to", code="bad_period")
    rows = (
        (
            await db.execute(
                select(Transaction)
                .where(
                    Transaction.account_id == principal.account_id,
                    Transaction.created_at >= from_ts,
                    Transaction.created_at <= to_ts,
                )
                .order_by(Transaction.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    customer = await _build_customer_info(db, account)
    pdf_bytes = render_period_invoice(
        transactions=rows,
        period_from=from_ts,
        period_to=to_ts,
        customer=customer,
        gateway=_gateway_info(),
    )
    log.info(
        "period_invoice_issued",
        account_id=str(principal.account_id),
        rows=len(rows),
        size_bytes=len(pdf_bytes),
    )
    filename = f"voltari-invoice-{from_ts.strftime('%Y%m%d')}-{to_ts.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/documents/upd",
    summary="PDF: УПД за период",
    description=(
        "Universal transfer document (УПД). Default ``vat_rate=0`` "
        "renders «Без НДС» — standard for ИП на УСН / самозанятого. "
        "For accounts with НДС, pass ``vat_rate=0.20`` to break out НДС "
        "from the gross."
    ),
    responses={200: {"content": {"application/pdf": {}}, "description": "PDF document"}},
)
async def get_period_upd(
    principal: Annotated[Principal, Depends(require_api_key_or_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    from_ts: datetime = Query(alias="from"),
    to_ts: datetime = Query(alias="to"),
    vat_rate: float = Query(default=0.0, ge=0.0, le=0.5),
) -> Response:
    if to_ts < from_ts:
        raise invalid_request("`to` must be >= `from`.", param="to", code="bad_period")
    rows = (
        (
            await db.execute(
                select(Transaction)
                .where(
                    Transaction.account_id == principal.account_id,
                    Transaction.created_at >= from_ts,
                    Transaction.created_at <= to_ts,
                )
                .order_by(Transaction.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    # Sprint 8 — legal threshold gate. УПД is mandatory only for the 10000₽+
    # cohort (per ФНС 402-ФЗ practice: small-period activity goes through
    # the period-invoice form, not УПД). Aggregate over signed amounts —
    # the doc covers a billing period, not a single ledger entry.
    period_total = sum(abs(r.amount_kopecks) for r in rows)
    if period_total < UPD_MIN_AMOUNT_KOPECKS:
        raise invalid_request(
            (
                f"Period activity {period_total // 100} ₽ is below the "
                f"{UPD_MIN_AMOUNT_KOPECKS // 100} ₽ УПД threshold; "
                "use the period-invoice endpoint instead."
            ),
            param="from",
            code="amount_below_upd_threshold",
        )
    account = await db.get(Account, principal.account_id)
    if account is None:
        raise authentication_error("Account not found.")
    customer = await _build_customer_info(db, account)
    pdf_bytes = render_upd(
        transactions=rows,
        period_from=from_ts,
        period_to=to_ts,
        customer=customer,
        gateway=_gateway_info(),
        vat_rate=vat_rate,
    )
    log.info(
        "upd_issued",
        account_id=str(principal.account_id),
        rows=len(rows),
        vat_rate=vat_rate,
        size_bytes=len(pdf_bytes),
    )
    filename = f"voltari-upd-{from_ts.strftime('%Y%m%d')}-{to_ts.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# Card linking + subscription (Phase 1, Pivot 2026-05-15)
# ============================================================================
#
# The three endpoints below all use ``require_session`` (cookie auth), NOT
# ``require_api_key_or_session``. Rationale: linking a card and changing
# subscription state are dashboard-level actions; an API-key holder who
# wandered into these would be a UX bug (and a security one — a stolen
# key should not be able to swap the saved card). The session cookie is
# also bound to the CSRF double-submit pair via ``require_session``.


_DEFAULT_BILLING_RETURN_URL = "https://brikko.ru/app/billing"


@router.post(
    "/link-card",
    response_model=LinkCardResponse,
    summary="Initiate card-link verification (1 ₽ → refund → save pm_id + 100 ₽ welcome)",
    description=(
        "Creates a 1 ₽ ЮKassa payment with ``save_payment_method=true`` "
        "and returns the confirmation URL. After the user confirms, ЮKassa "
        "calls our webhook; the handler refunds the 1 ₽, saves the "
        "``payment_method.id`` to ``account.autorefill_pm_id``, and "
        "credits the 100 ₽ card-link welcome bonus. **Idempotent guard:** "
        "rejects with 400 ``card_already_linked`` if the account already "
        "has a saved card, and 400 ``welcome_card_link_already_granted`` if "
        "the welcome bonus has been redeemed before (defence against "
        "remove-and-re-link)."
    ),
    responses={
        200: {"description": "Returns confirmation_url for ЮKassa redirect."},
        400: {"description": "Card already linked or welcome bonus already granted."},
        502: {"description": "ЮKassa error during payment creation."},
    },
)
async def link_card(
    body: LinkCardRequest,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    yookassa: Annotated[YooKassaClient, Depends(get_yookassa)],
) -> LinkCardResponse:
    account = principal.account
    if await has_card_already_linked(db, account):
        raise invalid_request(
            "A card is already linked to this account. Remove it first to link a new one.",
            param="autorefill_pm_id",
            code="card_already_linked",
        )
    if await has_received_card_link_welcome(db, account.id):
        # The user previously linked a card, claimed the welcome, then
        # removed it. Re-linking is fine for autorefill, but the bonus
        # is one-time per account.
        raise invalid_request(
            "Card-link welcome bonus has already been granted on this account.",
            param="account_id",
            code="welcome_card_link_already_granted",
        )

    try:
        result = await yookassa.create_payment(
            account_id=account.id,
            amount_kopecks=CARD_LINK_VERIFY_AMOUNT_KOPECKS,
            description="Brikko card-link verification (1 ₽, will be refunded)",
            return_url=body.return_url or _DEFAULT_BILLING_RETURN_URL,
            save_payment_method=True,
            metadata={
                "account_id": str(account.id),
                "purpose": CARD_LINK_PURPOSE,
            },
        )
    except YooKassaError as exc:
        log.warning(
            "card_link_yookassa_create_failed",
            account_id=str(account.id),
            error=str(exc),
        )
        raise upstream_error("Could not initiate card link with the bank gateway.") from exc

    log.info(
        "card_link_payment_created",
        account_id=str(account.id),
        payment_id=result.payment_id,
    )
    return LinkCardResponse(
        confirmation_url=result.confirmation_url,
        payment_id=result.payment_id,
    )


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=202,
    summary="Charge the linked card for a Pro/Team monthly subscription",
    description=(
        "Charges the saved card via ЮKassa recurring API and returns "
        "**202 Accepted** — the actual tier activation happens in the "
        "webhook handler (``payment.succeeded`` with "
        "``metadata.purpose=subscription_charge``). The frontend should "
        "poll ``GET /v1/billing/subscription`` or wait for an SSE event "
        "(Phase 2) to confirm activation.\n\n"
        "Requires a previously-linked card (``autorefill_pm_id != NULL``). "
        "Returns 412 ``card_not_linked`` otherwise."
    ),
    responses={
        202: {"description": "Charge initiated; activation pending webhook."},
        412: {"description": "No card linked. Call /v1/billing/link-card first."},
        502: {"description": "ЮKassa error during charge creation."},
    },
)
async def subscribe(
    body: SubscribeRequest,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
    yookassa: Annotated[YooKassaClient, Depends(get_yookassa)],
) -> SubscribeResponse:
    account = principal.account
    if account.autorefill_pm_id is None:
        raise GatewayError(
            status_code=412,
            message="Привяжите карту в настройках перед оформлением подписки.",
            type="invalid_request_error",
            code="card_not_linked",
        )

    tier = body.tier
    amount_kopecks = price_for_tier(tier)

    try:
        result = await yookassa.charge_recurring(
            account_id=account.id,
            amount_kopecks=amount_kopecks,
            payment_method_id=account.autorefill_pm_id,
            description=f"Brikko {tier.upper()} subscription, 30 days",
            metadata={
                "account_id": str(account.id),
                "purpose": "subscription_charge",
                "tier": tier,
            },
        )
    except YooKassaError as exc:
        log.warning(
            "subscribe_yookassa_charge_failed",
            account_id=str(account.id),
            tier=tier,
            error=str(exc),
        )
        raise upstream_error("Could not initiate subscription charge with the bank gateway.") from exc

    log.info(
        "subscribe_initiated",
        account_id=str(account.id),
        tier=tier,
        payment_id=result.payment_id,
        amount_kopecks=amount_kopecks,
        status=result.status,
    )
    return SubscribeResponse(
        status="pending",
        payment_id=result.payment_id,
        tier=tier,
        amount_kopecks=amount_kopecks,
    )


@router.post(
    "/subscribe/cancel",
    status_code=200,
    summary="Cancel active subscription (keeps access until active_until)",
    description=(
        "Sets ``subscription_canceled_at = now()``. The tier keeps working "
        "through ``subscription_active_until`` (user already paid for the "
        "period). Phase 2 cron will then NOT auto-renew. Calling cancel on "
        "an already-cancelled or PAYG account is a no-op (200 with "
        "``cancelled=false``)."
    ),
)
async def subscribe_cancel(
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    account_id = principal.account.id
    cancelled = await cancel_subscription(db, account_id=account_id)
    if cancelled:
        await write_audit(
            db,
            user_id=principal.user.id,
            account_id=account_id,
            action="subscription_cancelled",
            request=request,
            meta={"tier": principal.account.subscription_tier},
        )
    await db.commit()
    return {
        "cancelled": cancelled,
        "tier": principal.account.subscription_tier,
        "active_until": (
            principal.account.subscription_active_until.isoformat()
            if principal.account.subscription_active_until
            else None
        ),
    }


@router.delete(
    "/card",
    status_code=200,
    summary="Unlink the saved payment method (autorefill_pm_id) from the account",
    description=(
        "Clears ``account.autorefill_pm_id`` and disables autorefill. The "
        "welcome credit already granted (100 ₽ on card-link) is **not** "
        "clawed back. We do not call ЮKassa to delete the saved card on "
        "their side — payment methods are scoped to the merchant, so the "
        "handle becomes irrelevant the moment we forget it.\n\n"
        "If an active subscription is still running, returns "
        "``active_subscription_warning=true``. The user keeps tier access "
        "through the existing ``active_until``; the next renewal sweep "
        "will fail (no card) and dunning kicks in. Frontend should warn "
        "the user before this call."
    ),
    responses={
        200: {"description": "Card unlinked (or never linked, idempotent)."},
    },
)
async def unlink_card(
    request: Request,
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Forget the saved card. Welcome credit stays. Active sub keeps running.

    Edge cases:
      * No card linked → return ``ok=true, message="no_card_linked"`` (200,
        idempotent).
      * Active subscription → set warning flag in response so the frontend
        can confirm.
      * autorefill_enabled was True → also disable (a card-less autorefill
        loop would crash on every tick).
    """
    account = principal.account
    had_card = account.autorefill_pm_id is not None
    active_until = account.subscription_active_until
    if active_until is not None and active_until.tzinfo is None:
        active_until = active_until.replace(tzinfo=UTC)
    had_active_sub = (
        account.subscription_tier in PAID_TIERS
        and active_until is not None
        and active_until > datetime.now(UTC)
    )

    if not had_card:
        return {
            "ok": True,
            "message": "no_card_linked",
            "active_subscription_warning": had_active_sub,
        }

    account.autorefill_pm_id = None
    account.autorefill_enabled = False
    await db.flush()

    await write_audit(
        db,
        user_id=principal.user.id,
        account_id=account.id,
        action="card_unlinked",
        request=request,
        meta={"had_active_subscription": had_active_sub},
    )
    await db.commit()

    log.info(
        "card_unlinked",
        account_id=str(account.id),
        had_active_subscription=had_active_sub,
    )
    return {
        "ok": True,
        "message": "unlinked",
        "active_subscription_warning": had_active_sub,
    }


@router.get(
    "/subscription",
    response_model=SubscriptionResponse,
    summary="Read current subscription state",
    description=(
        "Returns ``tier`` (payg/pro/team), ``active_until``, ``canceled_at``, "
        "and card-link state. ``card_last4`` is reserved for Phase 2 (we'd "
        "have to fetch the payment-method from ЮKassa to populate it); "
        "Phase 1 always returns NULL."
    ),
)
async def get_subscription(
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> SubscriptionResponse:
    account = principal.account
    return SubscriptionResponse(
        tier=account.subscription_tier,
        active_until=account.subscription_active_until,
        canceled_at=account.subscription_canceled_at,
        card_linked=account.autorefill_pm_id is not None,
        card_last4=None,  # Phase 2
    )
