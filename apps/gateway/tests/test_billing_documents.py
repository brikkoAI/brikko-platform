"""Tests for Comply Pack PDF generation (Sprint 4 Поток M).

Two layers:

* Renderer-level: `render_akt_for_transaction` / `render_period_invoice`
  / `render_upd` — assert the bytes start with %PDF and aren't empty.
* HTTP-level: GET /v1/billing/documents/{txn}/akt etc. — auth, content-type,
  Content-Disposition, non-empty body.

Cyrillic font handling is tolerant: if DejaVu isn't installed in the test
runner, fpdf2 falls back to Helvetica and the document still issues — the
PDF will have boxes for cyrillic but byte-output is what we assert.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.billing.documents import (
    CustomerInfo,
    GatewayInfo,
    render_akt_for_transaction,
    render_period_invoice,
    render_upd,
)
from voltari_gateway.db.models import Transaction, TransactionKind


def _mk_txn(kind: TransactionKind = TransactionKind.TOPUP, amount: int = 100_000) -> Transaction:
    """Build a Transaction-shaped object (not persisted to DB)."""
    tx = Transaction(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        type=kind,
        amount_kopecks=amount,
        ref_id="test-ref-1",
        meta={"model": "gpt-5.4-mini"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return tx


def _customer() -> CustomerInfo:
    return CustomerInfo(
        full_name="ООО Ромашка",
        inn="7707083893",
        kpp="770701001",
        address="г. Москва, ул. Тверская, д. 1",
        email="ops@romashka.ru",
    )


def _gateway() -> GatewayInfo:
    return GatewayInfo()


# ---------- renderer tests --------------------------------------------------


def test_render_akt_returns_pdf_bytes() -> None:
    pdf = render_akt_for_transaction(
        transaction=_mk_txn(),
        customer=_customer(),
        gateway=_gateway(),
    )
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1024  # A4 page with header + table > 1 KB always


def test_render_akt_for_charge_handles_negative_amount() -> None:
    """CHARGE rows have amount_kopecks < 0; renderer must show absolute value."""
    pdf = render_akt_for_transaction(
        transaction=_mk_txn(kind=TransactionKind.CHARGE, amount=-540),
        customer=_customer(),
        gateway=_gateway(),
    )
    assert pdf.startswith(b"%PDF-")


def test_render_period_invoice_with_rows() -> None:
    txns = [_mk_txn(amount=100_000), _mk_txn(kind=TransactionKind.CHARGE, amount=-1234)]
    now = datetime.now(UTC)
    pdf = render_period_invoice(
        transactions=txns,
        period_from=now - timedelta(days=30),
        period_to=now,
        customer=_customer(),
        gateway=_gateway(),
    )
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1024


def test_render_period_invoice_empty_period_still_issues() -> None:
    """Zero transactions → still issues a PDF with «нет операций» note."""
    now = datetime.now(UTC)
    pdf = render_period_invoice(
        transactions=[],
        period_from=now - timedelta(days=30),
        period_to=now,
        customer=_customer(),
        gateway=_gateway(),
    )
    assert pdf.startswith(b"%PDF-")


def test_render_upd_default_no_vat() -> None:
    txns = [_mk_txn(amount=120_000)]
    now = datetime.now(UTC)
    pdf = render_upd(
        transactions=txns,
        period_from=now - timedelta(days=30),
        period_to=now,
        customer=_customer(),
        gateway=_gateway(),
        # vat_rate=0 — default
    )
    assert pdf.startswith(b"%PDF-")


def test_render_upd_with_vat_rate() -> None:
    txns = [_mk_txn(amount=120_000)]
    now = datetime.now(UTC)
    pdf = render_upd(
        transactions=txns,
        period_from=now - timedelta(days=30),
        period_to=now,
        customer=_customer(),
        gateway=_gateway(),
        vat_rate=0.20,
    )
    assert pdf.startswith(b"%PDF-")


def test_render_with_missing_customer_fields() -> None:
    """Customer with no INN / KPP / address — still issues, with placeholders."""
    minimal_customer = CustomerInfo(full_name="ИП Иванов")
    pdf = render_akt_for_transaction(
        transaction=_mk_txn(),
        customer=minimal_customer,
        gateway=_gateway(),
    )
    assert pdf.startswith(b"%PDF-")


# ---------- HTTP-level integration tests -----------------------------------


@pytest.mark.asyncio
async def test_http_akt_for_existing_transaction(client, app, api_key_fixture, db) -> None:
    """POST a transaction first (top-up to seed), then GET its akt.

    Uses a 1500 ₽ top-up — comfortably above the Sprint 8 acт threshold
    (1000 ₽). Sub-threshold path is covered separately in
    ``test_http_akt_below_threshold_400``.
    """
    from voltari_gateway.billing.engine import credit_account

    tx = await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=1_500_00,
        ref_id="seed-txn-1",
        kind=TransactionKind.TOPUP,
        meta={"source": "test"},
    )
    await db.commit()

    resp = await client.get(
        f"/v1/billing/documents/{tx.id}/akt",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")
    assert len(resp.content) > 1024


@pytest.mark.asyncio
async def test_http_akt_for_other_account_404(client, api_key_fixture) -> None:
    """Random UUID → 404, no information leak about whose txn it might be."""
    fake_uuid = uuid.uuid4()
    resp = await client.get(
        f"/v1/billing/documents/{fake_uuid}/akt",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_http_period_invoice(client, api_key_fixture, db) -> None:
    """Period invoice with one TOPUP transaction inside."""
    from voltari_gateway.billing.engine import credit_account

    await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=500_00,
        ref_id="period-seed-1",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    now = datetime.now(UTC)
    resp = await client.get(
        "/v1/billing/documents/period",
        params={
            "from": (now - timedelta(days=7)).isoformat(),
            "to": (now + timedelta(hours=1)).isoformat(),
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_http_upd_default_no_vat(client, api_key_fixture, db) -> None:
    """Period activity ≥ 10000 ₽ → УПД issues OK (Sprint 8 threshold)."""
    from voltari_gateway.billing.engine import credit_account

    await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=15_000_00,
        ref_id="upd-seed-1",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    now = datetime.now(UTC)
    resp = await client.get(
        "/v1/billing/documents/upd",
        params={
            "from": (now - timedelta(days=7)).isoformat(),
            "to": (now + timedelta(hours=1)).isoformat(),
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_http_period_invoice_invalid_range(client, api_key_fixture) -> None:
    now = datetime.now(UTC)
    resp = await client.get(
        "/v1/billing/documents/period",
        params={
            "from": now.isoformat(),
            "to": (now - timedelta(days=1)).isoformat(),  # to < from
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_http_documents_unauthenticated(client) -> None:
    resp = await client.get(
        f"/v1/billing/documents/{uuid.uuid4()}/akt",
    )
    assert resp.status_code == 401


# ---------- Sprint 8: legal-document amount thresholds ---------------------
#
# Акт минимальная сумма = 1000 ₽. УПД минимальная (за период) = 10000 ₽.
# These constants are public — clients are told the policy in /docs and
# tests assert the public constant rather than the literal so a policy
# change in legal docs flows through without orphaned magic numbers.


def test_threshold_constants_match_public_policy() -> None:
    """Public constants stay aligned with the documented policy.

    If legal updates the policy (e.g. drops акт минимум to 500₽), this
    test fails as a tripwire — bump the constant in `documents.py` and
    update `02_Product/v1.5/12_docs_page_content.md`.
    """
    from voltari_gateway.billing.documents import (
        AKT_MIN_AMOUNT_KOPECKS,
        UPD_MIN_AMOUNT_KOPECKS,
    )

    assert AKT_MIN_AMOUNT_KOPECKS == 1_000_00, "policy: акт от 1000 ₽"
    assert UPD_MIN_AMOUNT_KOPECKS == 10_000_00, "policy: УПД от 10000 ₽"


@pytest.mark.asyncio
async def test_http_akt_below_threshold_400(client, api_key_fixture, db) -> None:
    """Tx under 1000 ₽ → 400 with code=amount_below_akt_threshold."""
    from voltari_gateway.billing.engine import credit_account

    tx = await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=500_00,  # 500 ₽ — below акт threshold
        ref_id="below-akt-seed",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    resp = await client.get(
        f"/v1/billing/documents/{tx.id}/akt",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "amount_below_akt_threshold"


@pytest.mark.asyncio
async def test_http_akt_at_threshold_ok(client, api_key_fixture, db) -> None:
    """Tx exactly at 1000 ₽ → акт issues OK (boundary inclusive)."""
    from voltari_gateway.billing.engine import credit_account

    tx = await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=1_000_00,
        ref_id="at-akt-seed",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    resp = await client.get(
        f"/v1/billing/documents/{tx.id}/akt",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_http_upd_period_below_threshold_400(client, api_key_fixture, db) -> None:
    """Period activity < 10000 ₽ → УПД refused with 400."""
    from voltari_gateway.billing.engine import credit_account

    # 1500₽ → акт-eligible (>=1000) but УПД-ineligible (<10000)
    await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=1_500_00,
        ref_id="upd-below-seed",
        kind=TransactionKind.TOPUP,
    )
    await db.commit()

    now = datetime.now(UTC)
    resp = await client.get(
        "/v1/billing/documents/upd",
        params={
            "from": (now - timedelta(days=7)).isoformat(),
            "to": (now + timedelta(hours=1)).isoformat(),
        },
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "amount_below_upd_threshold"


@pytest.mark.asyncio
async def test_http_akt_for_refund_uses_absolute_value(client, api_key_fixture, db) -> None:
    """A −2000 ₽ refund row IS akt-eligible (|−2000| ≥ 1000)."""
    from voltari_gateway.billing.engine import credit_account, refund_account

    # Seed a 5000₽ topup so the account has balance to refund against
    # (refund_account otherwise stalls on the overdraft floor).
    await credit_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=5_000_00,
        ref_id="seed-for-refund",
        kind=TransactionKind.TOPUP,
    )
    refund_tx = await refund_account(
        db,
        account_id=api_key_fixture.account.id,
        amount_kopecks=2_000_00,
        ref_id="refund-1",
    )
    await db.commit()

    resp = await client.get(
        f"/v1/billing/documents/{refund_tx.id}/akt",
        headers=api_key_fixture.auth_header,
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")
