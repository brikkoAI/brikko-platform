"""Subscription Phase 2 cron tests — renewal / retry / reminders / downgrade.

Coverage map (against the BRIEF list):
  * test_renew_creates_yookassa_charge_for_due_subscription
  * test_renew_skips_canceled_subscriptions
  * test_renew_skips_already_renewed_idempotency
  * test_failed_renewal_marks_retry_count
  * test_3rd_failed_renewal_downgrades_to_payg
  * test_reminder_sent_72h_before_renewal
  * test_reminder_not_sent_twice_per_period
  * test_transient_yookassa_error_does_not_bump_counter

The tests live close to the cron logic (not the API surface) so they
can drive ``renew_due_subscriptions`` / ``retry_failed_renewals`` /
``downgrade_exhausted_subscriptions`` / ``send_renewal_reminders``
directly with controlled clocks and a mocked ЮKassa client.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from voltari_gateway.billing.subscription import (
    MAX_RENEWAL_RETRIES,
    RENEWAL_RETRY_DELAY_HOURS,
    TIER_PAYG,
)
from voltari_gateway.billing.subscription_emails import (
    REMINDER_T_MINUS_3,
    EmailDispatcher,
    send_renewal_reminders,
)
from voltari_gateway.billing.subscription_renewal import (
    downgrade_exhausted_subscriptions,
    renew_due_subscriptions,
    retry_failed_renewals,
)
from voltari_gateway.billing.yookassa import (
    PaymentURL,
    YooKassaError,
)
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    SubscriptionReminderSent,
    Tariff,
    User,
)

# ---------- helpers ---------------------------------------------------------


@dataclass
class FakeYooKassa:
    """Test double — captures recurring charge args + returns canned status."""

    next_status: str = "succeeded"
    next_payment_id: str = "pay-fake-1"
    raise_error: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    async def charge_recurring(self, **kw) -> PaymentURL:
        self.calls.append(kw)
        if self.raise_error is not None:
            raise self.raise_error
        return PaymentURL(
            payment_id=self.next_payment_id,
            confirmation_url="",
            amount_kopecks=kw["amount_kopecks"],
            status=self.next_status,
        )

    async def aclose(self) -> None:
        return None


@dataclass
class FakeEmailSender:
    """Captures (to, subject, body) tuples in lieu of SMTP."""

    sent: list[tuple[str, str, str]] = field(default_factory=list)

    async def __call__(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest_asyncio.fixture
async def seeded_pro(db) -> tuple[User, Account]:
    user = User(
        email=f"renewal-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="Renewal probe",
        balance_kopecks=0,
        tariff=Tariff.PAYG,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
        settings={},
        subscription_tier="pro",
        subscription_active_until=datetime.now(UTC) + timedelta(hours=12),
        autorefill_pm_id="pmid-saved-1",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return user, account


# ---------- renew_due_subscriptions -----------------------------------------


@pytest.mark.asyncio
async def test_renew_creates_yookassa_charge_for_due_subscription(
    db, session_factory, seeded_pro
):
    _user, account = seeded_pro
    yk = FakeYooKassa(next_status="pending")  # ЮKassa accepted, webhook will activate
    sender = FakeEmailSender()
    email = EmailDispatcher(session_factory=session_factory, sender=sender)

    attempted = await renew_due_subscriptions(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
        email=email,
    )
    assert attempted == 1
    assert len(yk.calls) == 1
    call = yk.calls[0]
    assert call["payment_method_id"] == "pmid-saved-1"
    assert call["metadata"]["purpose"] == "subscription_charge"
    assert call["metadata"]["renewal_attempt"] == "1"
    # Counter must remain 0 — pending isn't a fail.
    await db.refresh(account)
    assert account.renewal_retry_count == 0
    # No email yet — failure-mail only fires on canceled.
    assert sender.sent == []


@pytest.mark.asyncio
async def test_renew_skips_canceled_subscriptions(
    db, session_factory, seeded_pro
):
    _user, account = seeded_pro
    account.subscription_canceled_at = datetime.now(UTC)
    db.add(account)
    await db.commit()

    yk = FakeYooKassa()
    attempted = await renew_due_subscriptions(
        session_factory=session_factory, yookassa=yk  # type: ignore[arg-type]
    )
    assert attempted == 0
    assert yk.calls == []


@pytest.mark.asyncio
async def test_renew_skips_already_renewed_idempotency(
    db, session_factory, seeded_pro
):
    """Account already in retry_count>0 belongs to the retry sweep, not slot-1."""
    _user, account = seeded_pro
    account.renewal_retry_count = 1
    account.renewal_last_failed_at = datetime.now(UTC) - timedelta(hours=1)
    db.add(account)
    await db.commit()

    yk = FakeYooKassa()
    attempted = await renew_due_subscriptions(
        session_factory=session_factory, yookassa=yk  # type: ignore[arg-type]
    )
    assert attempted == 0
    assert yk.calls == []


@pytest.mark.asyncio
async def test_failed_renewal_marks_retry_count(
    db, session_factory, seeded_pro
):
    """ЮKassa returns status=canceled → counter bumps to 1, email fires."""
    _user, account = seeded_pro
    yk = FakeYooKassa(next_status="canceled")
    sender = FakeEmailSender()
    email = EmailDispatcher(session_factory=session_factory, sender=sender)

    attempted = await renew_due_subscriptions(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
        email=email,
    )
    assert attempted == 1
    await db.refresh(account)
    assert account.renewal_retry_count == 1
    assert account.renewal_last_failed_at is not None
    # One email queued — "we'll retry in {RENEWAL_RETRY_DELAY_HOURS[0]}h"
    assert len(sender.sent) == 1
    assert "не удалось" in sender.sent[0][1].lower() or "не удалось" in sender.sent[0][2].lower()


@pytest.mark.asyncio
async def test_transient_yookassa_error_does_not_bump_counter(
    db, session_factory, seeded_pro
):
    """5xx / network blip is *transient* — we just exit and try again next tick."""
    _user, account = seeded_pro
    yk = FakeYooKassa(raise_error=YooKassaError("yookassa_unreachable: ECONNREFUSED"))

    attempted = await renew_due_subscriptions(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
    )
    assert attempted == 1  # we attempted, just didn't burn a slot
    await db.refresh(account)
    assert account.renewal_retry_count == 0
    assert account.renewal_last_failed_at is None


# ---------- retry sweep -----------------------------------------------------


@pytest.mark.asyncio
async def test_retry_runs_after_24h_when_first_attempt_failed(
    db, session_factory, seeded_pro
):
    _user, account = seeded_pro
    account.renewal_retry_count = 1
    account.renewal_last_failed_at = datetime.now(UTC) - timedelta(
        hours=RENEWAL_RETRY_DELAY_HOURS[0] + 1
    )
    db.add(account)
    await db.commit()

    yk = FakeYooKassa(next_status="canceled")
    sender = FakeEmailSender()
    email = EmailDispatcher(session_factory=session_factory, sender=sender)
    retried = await retry_failed_renewals(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
        email=email,
    )
    assert retried == 1
    assert yk.calls[0]["metadata"]["renewal_attempt"] == "2"
    await db.refresh(account)
    assert account.renewal_retry_count == 2


@pytest.mark.asyncio
async def test_retry_resets_counter_on_success(
    db, session_factory, seeded_pro
):
    """When ЮKassa accepts the retry, counter resets to 0 (webhook will activate)."""
    _user, account = seeded_pro
    account.renewal_retry_count = 1
    account.renewal_last_failed_at = datetime.now(UTC) - timedelta(
        hours=RENEWAL_RETRY_DELAY_HOURS[0] + 1
    )
    db.add(account)
    await db.commit()

    yk = FakeYooKassa(next_status="pending")
    retried = await retry_failed_renewals(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
    )
    assert retried == 1
    await db.refresh(account)
    assert account.renewal_retry_count == 0
    assert account.renewal_last_failed_at is None


@pytest.mark.asyncio
async def test_3rd_failed_renewal_downgrades_to_payg(
    db, session_factory, seeded_pro
):
    """retry_count=2 + canceled → count=3 → downgrade sweep flips to payg."""
    _user, account = seeded_pro
    account.renewal_retry_count = 2
    account.renewal_last_failed_at = datetime.now(UTC) - timedelta(
        hours=RENEWAL_RETRY_DELAY_HOURS[1] + 1
    )
    db.add(account)
    await db.commit()

    yk = FakeYooKassa(next_status="canceled")
    sender = FakeEmailSender()
    email = EmailDispatcher(session_factory=session_factory, sender=sender)

    retried = await retry_failed_renewals(
        session_factory=session_factory,
        yookassa=yk,  # type: ignore[arg-type]
        email=email,
    )
    assert retried == 1
    await db.refresh(account)
    assert account.renewal_retry_count == MAX_RENEWAL_RETRIES  # 3
    # Now run the downgrade sweep.
    downgraded = await downgrade_exhausted_subscriptions(
        session_factory=session_factory,
        email=email,
    )
    assert downgraded == 1
    await db.refresh(account)
    assert account.subscription_tier == TIER_PAYG
    assert account.subscription_active_until is None
    assert account.renewal_retry_count == 0
    # Downgrade email landed. We accept any email whose subject or body
    # mentions the downgrade — the exact copy may shift between sprints.
    downgrade_emails = [
        s
        for s in sender.sent
        if "не продлилась" in (s[1] + s[2]).lower() or "трал" in (s[1] + s[2]).lower()
    ]
    assert downgrade_emails, sender.sent


# ---------- reminder cron ---------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_sent_72h_before_renewal(
    db, session_factory, seeded_pro
):
    _user, account = seeded_pro
    account.subscription_active_until = datetime.now(UTC) + timedelta(hours=80)
    db.add(account)
    await db.commit()

    sender = FakeEmailSender()
    sent = await send_renewal_reminders(
        session_factory=session_factory, send=sender
    )
    assert sent == 1
    assert len(sender.sent) == 1
    subject, body = sender.sent[0][1], sender.sent[0][2]
    assert "Pro" in subject
    assert "3 дня" in body

    # A row was written to subscription_reminders_sent.
    rows = (
        await db.execute(
            select(SubscriptionReminderSent).where(
                SubscriptionReminderSent.account_id == account.id,
                SubscriptionReminderSent.reminder_type == REMINDER_T_MINUS_3,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_reminder_not_sent_twice_per_period(
    db, session_factory, seeded_pro
):
    """Second call in the same day must short on the UNIQUE constraint."""
    _user, account = seeded_pro
    account.subscription_active_until = datetime.now(UTC) + timedelta(hours=80)
    db.add(account)
    await db.commit()

    sender = FakeEmailSender()
    first = await send_renewal_reminders(
        session_factory=session_factory, send=sender
    )
    second = await send_renewal_reminders(
        session_factory=session_factory, send=sender
    )
    assert first == 1
    assert second == 0  # idempotent
    assert len(sender.sent) == 1


@pytest.mark.asyncio
async def test_reminder_skips_canceled_accounts(
    db, session_factory, seeded_pro
):
    _user, account = seeded_pro
    account.subscription_active_until = datetime.now(UTC) + timedelta(hours=80)
    account.subscription_canceled_at = datetime.now(UTC)
    db.add(account)
    await db.commit()

    sender = FakeEmailSender()
    sent = await send_renewal_reminders(
        session_factory=session_factory, send=sender
    )
    assert sent == 0
    assert sender.sent == []
