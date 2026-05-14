"""Management API — tariff upgrade endpoint (Sprint 6, Блок 9).

Mounted at ``PATCH /v1/account/tariff``. Owner-only (the seat-role check is
implicit: only the owning user holds the cookie session bound to the
account; admins/members get 403 from the explicit owner check below).

Per CEO 29.04 (CONTEXT в задаче, ответ #3): on upgrade we **immediately
debit** the monthly fee from balance via ``billing.engine.debit_account``.
Idempotency key: ``subscription:{account_id}:{period_start_iso}``.
On insufficient balance → 402, no charge, no tariff change.

Downgrade to PAYG: forfeit-style (no refund of remaining days, like Slack /
Notion). Implemented as immediate flip without charge. We don't currently
support deferred downgrade (apply at period end) — that's a Sprint 7 task
and is documented in TD.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.billing.engine import (
    BillingError,
    InsufficientBalanceError,
    debit_account,
)
from voltari_gateway.db.models import Tariff, TransactionKind
from voltari_gateway.db.session import get_db
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/account", tags=["account"])


# ---------------------------------------------------------------------------
# Pricing — fixed in finance model M3 (BRIEF + 03_Finance/04).
# ---------------------------------------------------------------------------

# Monthly fee in kopecks. PAYG = 0 (no charge on activation), TEAM/etc.
# locked here until we move them to the DB-driven plan catalogue.
TARIFF_MONTHLY_FEE_KOPECKS: Final[dict[Tariff, int]] = {
    Tariff.PAYG: 0,
    Tariff.PRO: 199_000,  # 1 990 ₽
    Tariff.PRO_PRIVACY: 279_000,  # 2 790 ₽
    Tariff.TEAM: 499_000,  # 4 990 ₽ (CEO 2026-05-09 — выровнено с лендингом)
    # BUSINESS / BUSINESS_PLUS — закрыты для signup'а с 2026-05-09 (rework
    # под самозанятого, см. _account_to_response). Оставлены в enum как
    # legacy — существующие аккаунты, если такие есть, продолжают работать.
    Tariff.BUSINESS: 1_999_000,  # legacy, недоступен через PATCH
    Tariff.BUSINESS_PLUS: 10_000_000,  # legacy, недоступен через PATCH
}

# Тарифы, доступные для self-service signup / upgrade.
# Самозанятый-формат (ст. 2 ФЗ-422) не позволяет выдавать акты, УПД, ЭДО,
# заключать двусторонние договоры услуг — поэтому Business / Business+
# выведены из публичного каталога. CEO решение 2026-05-09.
PUBLIC_TARIFFS: Final[frozenset[Tariff]] = frozenset(
    {Tariff.PAYG, Tariff.PRO, Tariff.PRO_PRIVACY, Tariff.TEAM}
)

# Tariff "rank" for upgrade vs downgrade comparisons.
_RANK: Final[dict[Tariff, int]] = {
    Tariff.PAYG: 0,
    Tariff.PRO: 1,
    Tariff.PRO_PRIVACY: 2,
    Tariff.TEAM: 3,
    Tariff.BUSINESS: 4,
    Tariff.BUSINESS_PLUS: 5,
}

PERIOD_DAYS: Final[int] = 30


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChangeTariffRequest(BaseModel):
    tariff: Tariff
    # Caller-supplied confirmation phrase, must equal "UPGRADE" / "DOWNGRADE".
    # Frontend modal asks the user to type it as a soft-spoiler against
    # accidental clicks. Backend treats the value as informational; the
    # real protection is the session cookie + CSRF.
    confirmation: str = Field(min_length=1, max_length=32)


class ChangeTariffResponse(BaseModel):
    tariff: str
    previous_tariff: str
    charged_kopecks: int
    balance_kopecks: int
    tariff_active_until: datetime | None
    transaction_id: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _period_start_iso(now: datetime) -> str:
    """Day-precision period start used in idempotency keys.

    Two upgrade requests on the same calendar day from the same account
    collapse to one debit. This is a reasonable double-click guard
    without needing a Redis-backed Idempotency-Key store. A user who
    upgrades, downgrades and upgrades again the same day would NOT be
    re-charged — that's correct (we don't re-bill mid-cycle); the
    downgrade path forfeits.
    """
    return now.strftime("%Y-%m-%d")


def _ref_id(account_id: str, period_iso: str) -> str:
    return f"subscription:{account_id}:{period_iso}"


# ---------------------------------------------------------------------------
# PATCH /v1/account/tariff
# ---------------------------------------------------------------------------


@router.patch(
    "/tariff",
    response_model=ChangeTariffResponse,
    status_code=200,
    tags=["account"],
    summary="Change tariff (immediate charge for upgrade, forfeit for downgrade)",
    description=(
        "Owner-only. Upgrades charge the monthly fee from balance; "
        "downgrades to PAYG are forfeit (no refund). Idempotent on "
        "(account_id, period_start_day). When ``Idempotency-Key`` "
        "header is provided we use it as the ledger ref_id directly."
    ),
    responses={
        200: {"description": "Tariff updated."},
        400: {"description": "Same tariff, unsupported tariff, or invalid confirmation."},
        402: {"description": "Insufficient balance for the upgrade fee."},
        403: {"description": "Caller is not the account owner."},
    },
)
async def change_tariff(
    body: ChangeTariffRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ChangeTariffResponse:
    user = principal.user
    account = principal.account

    if account.owner_id != user.id:
        await write_audit(
            db,
            user_id=user.id,
            account_id=account.id,
            action="tariff_change_denied",
            outcome="denied",
            request=request,
            meta={"reason": "not_owner"},
        )
        await db.commit()
        raise GatewayError(
            status_code=403,
            message="Only the account owner can change the tariff.",
            type="invalid_request_error",
            code="not_owner",
        )

    new_tariff = body.tariff
    old_tariff = account.tariff
    if new_tariff == old_tariff:
        raise invalid_request(
            "Account is already on this tariff.",
            param="tariff",
            code="same_tariff",
        )

    if new_tariff not in TARIFF_MONTHLY_FEE_KOPECKS:
        raise invalid_request(
            f"Unsupported tariff: {new_tariff.value}.",
            param="tariff",
            code="unsupported_tariff",
        )
    if new_tariff not in PUBLIC_TARIFFS:
        # Business / Business+ закрыты для самозанятого формата (нет актов /
        # УПД / ЭДО / двусторонних договоров). Существующий аккаунт может
        # быть на legacy-тарифе и downgrade'нуть, но новые upgrades блокируем.
        raise invalid_request(
            f"Tariff '{new_tariff.value}' is not available for self-service signup.",
            param="tariff",
            code="tariff_unavailable",
        )

    new_fee = TARIFF_MONTHLY_FEE_KOPECKS[new_tariff]
    old_fee = TARIFF_MONTHLY_FEE_KOPECKS[old_tariff]
    new_rank = _RANK[new_tariff]
    old_rank = _RANK[old_tariff]
    is_upgrade = new_rank > old_rank

    # Confirmation phrase — light defence against accidental clicks.
    expected = "UPGRADE" if is_upgrade else "DOWNGRADE"
    if body.confirmation.strip().upper() != expected:
        raise invalid_request(
            f"Confirmation phrase must be exactly '{expected}'.",
            param="confirmation",
            code="bad_confirmation",
        )

    now = datetime.now(UTC)
    charged_kopecks = 0
    transaction_id: str | None = None

    # Sprint 11 — tag the upcoming Account.tariff change so the
    # ``before_flush`` listener in ``db/events.py`` records ``reason`` /
    # ``triggered_by`` on the TariffHistory row. The listener pops this
    # attribute on consumption, so it never lingers on the instance.
    account._tariff_change_meta = {  # type: ignore[attr-defined]
        "reason": "user_upgrade" if is_upgrade else "user_downgrade",
        "triggered_by": "user",
    }

    if is_upgrade:
        # Compute the charge. Two cases:
        #   PAYG → paid           : full new_fee.
        #   paid → higher paid    : prorate-free model — charge the *delta*
        #     so a Pro→Pro Privacy mid-cycle upgrade only costs the difference.
        #     Period anchor (tariff_active_until) is preserved.
        amount_to_charge = new_fee
        preserve_period = False
        if old_fee > 0 and account.tariff_active_until is not None:
            amount_to_charge = max(0, new_fee - old_fee)
            preserve_period = True

        if amount_to_charge > 0:
            ref_id = idempotency_key or _ref_id(str(account.id), _period_start_iso(now))
            try:
                # NB: TransactionKind.CHARGE is the right kind here because the
                # ledger CHECK constraint requires charge.amount < 0. The
                # ``meta.kind = "tariff_change"`` distinguishes it from a
                # request charge in reports.
                tx = await debit_account(
                    db,
                    account_id=account.id,
                    amount_kopecks=amount_to_charge,
                    ref_id=ref_id,
                    kind=TransactionKind.CHARGE,
                    meta={
                        "kind": "tariff_change",
                        "from": old_tariff.value,
                        "to": new_tariff.value,
                        "fee_kopecks": new_fee,
                        "delta_kopecks": amount_to_charge,
                    },
                )
                transaction_id = str(tx.id)
                charged_kopecks = amount_to_charge
            except InsufficientBalanceError as exc:
                # Snapshot identifiers BEFORE rollback — after rollback the
                # ORM expires attached objects and reading user.id would
                # try to lazy-reload, tripping MissingGreenlet inside the
                # exception path.
                _user_id = user.id
                _account_id = account.id
                await db.rollback()
                await write_audit(
                    db,
                    user_id=_user_id,
                    account_id=_account_id,
                    action="tariff_change_denied",
                    outcome="denied",
                    request=request,
                    meta={
                        "reason": "insufficient_balance",
                        "from": old_tariff.value,
                        "to": new_tariff.value,
                        "required_kopecks": exc.required_kopecks,
                        "balance_kopecks": exc.balance_kopecks,
                    },
                )
                await db.commit()
                raise GatewayError(
                    status_code=402,
                    message=(
                        f"Insufficient balance for upgrade. "
                        f"Required: {amount_to_charge / 100:.2f} ₽, "
                        f"have: {exc.balance_kopecks / 100:.2f} ₽."
                    ),
                    type="insufficient_quota",
                    code="insufficient_balance",
                ) from exc
            except BillingError as exc:
                await db.rollback()
                log.error("tariff_change_billing_error", error=str(exc))
                raise GatewayError(
                    status_code=500,
                    message="Could not process tariff change.",
                    type="api_error",
                    code="tariff_change_failed",
                ) from exc

        account.tariff = new_tariff
        # Preserve period anchor on prorated mid-cycle upgrades; reset on
        # PAYG → paid promotions.
        if not preserve_period or account.tariff_active_until is None:
            account.tariff_active_until = now + timedelta(days=PERIOD_DAYS)
    else:
        # Downgrade — forfeit. No refund of remaining days.
        account.tariff = new_tariff
        if new_tariff == Tariff.PAYG:
            account.tariff_active_until = None
        # Paid → cheaper paid: keep tariff_active_until until the period ends
        # so the user keeps the longer access they paid for. We did NOT charge.

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="tariff_changed",
        request=request,
        meta={
            "from": old_tariff.value,
            "to": new_tariff.value,
            "charged_kopecks": charged_kopecks,
            "is_upgrade": is_upgrade,
        },
    )
    await db.commit()
    await db.refresh(account)

    return ChangeTariffResponse(
        tariff=new_tariff.value,
        previous_tariff=old_tariff.value,
        charged_kopecks=charged_kopecks,
        balance_kopecks=int(account.balance_kopecks),
        tariff_active_until=account.tariff_active_until,
        transaction_id=transaction_id,
    )


__all__ = ["TARIFF_MONTHLY_FEE_KOPECKS", "router"]
