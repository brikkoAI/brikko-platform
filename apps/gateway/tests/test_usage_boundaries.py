"""Date-boundary tests for /v1/usage (TD-042 hotfix pinning).

Pin the inclusive-end-of-day semantics for ``to=YYYY-MM-DD`` queries.
Spec:

* ``to=YYYY-MM-DD`` is **inclusive** through 23:59:59.999999 UTC of that
  date (Stripe convention). Events created at 23:59:00 on the boundary
  date must be in the bucket.
* ``to=YYYY-MM-DD`` does NOT include events from the next day, even at
  00:00:01 UTC.
* ``from=YYYY-MM-DD`` is inclusive at 00:00:00 UTC. Events from the
  previous day at 23:59:59 must NOT appear.
* Full ISO timestamps are honoured exactly (no end-of-day fuzz).

The TD-042 bug was: ``_parse_iso`` for date-only ``to`` returned 00:00:00
of that date, so anything created after midnight on the requested day
was silently excluded — dashboard's "Расход за 7 дней" showed 0 right
after a real chat call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from voltari_gateway.auth.keys import generate_api_key
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyStatus,
    Tariff,
    UsageEvent,
    User,
)


async def _seed_account_with_key(db) -> tuple[ApiKey, Account, str]:
    user = User(
        email=f"usage-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="$argon2id$v=19$m=65536,t=2,p=2$dummy",
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    account = Account(
        owner_id=user.id,
        name="usage-bnd",
        balance_kopecks=100_000,
        tariff=Tariff.PRO,
        status=AccountStatus.ACTIVE,
        store_prompts=True,
    )
    db.add(account)
    await db.flush()
    generated = generate_api_key()
    api_key = ApiKey(
        account_id=account.id,
        name="usage-bnd",
        key_hash=generated.key_hash,
        key_prefix=generated.prefix,
        status=ApiKeyStatus.ACTIVE,
    )
    db.add(api_key)
    await db.commit()
    return api_key, account, generated.plaintext


async def _add_usage_event(
    db,
    *,
    account_id: uuid.UUID,
    api_key_id: uuid.UUID,
    when: datetime,
    cost_kop: int = 100,
    input_t: int = 50,
    output_t: int = 50,
) -> None:
    event = UsageEvent(
        account_id=account_id,
        api_key_id=api_key_id,
        model="gpt-5.4-mini",
        provider="openai",
        input_tokens=input_t,
        output_tokens=output_t,
        cached_tokens=0,
        cost_kopecks=cost_kop,
        request_id=f"req-{uuid.uuid4().hex[:12]}",
        created_at=when,
    )
    db.add(event)
    await db.commit()


# ----- to= inclusive end-of-day ----------------------------------------------


@pytest.mark.asyncio
async def test_to_yyyy_mm_dd_includes_late_evening(client, db):
    """``to=2026-04-29`` MUST include events at 23:59:00 UTC on the same day."""
    api_key, account, plaintext = await _seed_account_with_key(db)

    # Pick a fixed past date so we don't race the test clock.
    target_date = datetime(2026, 4, 29, tzinfo=UTC)
    late = target_date.replace(hour=23, minute=59, second=0, microsecond=0)

    await _add_usage_event(
        db, account_id=account.id, api_key_id=api_key.id, when=late, cost_kop=777
    )

    r = await client.get(
        "/v1/usage?from=2026-04-29&to=2026-04-29",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["cost_kop"] == 777, body
    assert body["totals"]["request_count"] == 1


@pytest.mark.asyncio
async def test_to_yyyy_mm_dd_excludes_next_day_first_second(client, db):
    """An event at 00:00:01 of *next* day MUST NOT be in ``to=today``."""
    api_key, account, plaintext = await _seed_account_with_key(db)

    target_date = datetime(2026, 4, 29, tzinfo=UTC)
    next_day_start = target_date + timedelta(days=1, seconds=1)

    await _add_usage_event(
        db, account_id=account.id, api_key_id=api_key.id, when=next_day_start, cost_kop=999
    )

    r = await client.get(
        "/v1/usage?from=2026-04-29&to=2026-04-29",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["cost_kop"] == 0, body
    assert body["totals"]["request_count"] == 0


# ----- from= inclusive at 00:00:00 -------------------------------------------


@pytest.mark.asyncio
async def test_from_yyyy_mm_dd_excludes_previous_day_last_second(client, db):
    """An event at 23:59:59 of the *previous* day MUST NOT be included
    when ``from=YYYY-MM-DD`` references the boundary day.
    """
    api_key, account, plaintext = await _seed_account_with_key(db)

    target_date = datetime(2026, 4, 29, tzinfo=UTC)
    previous_late = target_date - timedelta(seconds=1)  # 2026-04-28T23:59:59

    await _add_usage_event(
        db,
        account_id=account.id,
        api_key_id=api_key.id,
        when=previous_late,
        cost_kop=555,
    )

    r = await client.get(
        "/v1/usage?from=2026-04-29&to=2026-04-29",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["cost_kop"] == 0
    assert body["totals"]["request_count"] == 0


@pytest.mark.asyncio
async def test_from_yyyy_mm_dd_includes_first_microsecond(client, db):
    """An event at 00:00:00.000001 on the ``from`` date is included."""
    api_key, account, plaintext = await _seed_account_with_key(db)

    target = datetime(2026, 4, 29, 0, 0, 0, 1, tzinfo=UTC)

    await _add_usage_event(
        db, account_id=account.id, api_key_id=api_key.id, when=target, cost_kop=42
    )

    r = await client.get(
        "/v1/usage?from=2026-04-29&to=2026-04-29",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["cost_kop"] == 42
    assert body["totals"]["request_count"] == 1


# ----- ISO timestamps are honoured exactly (no end-of-day fuzz) --------------


@pytest.mark.asyncio
async def test_full_iso_timestamp_treated_as_exact(client, db):
    """Full ISO-8601 ``to=2026-04-29T12:00:00Z`` must NOT be expanded to
    end-of-day; an event at 13:00 same day must be excluded.
    """
    api_key, account, plaintext = await _seed_account_with_key(db)

    afternoon = datetime(2026, 4, 29, 13, 0, 0, tzinfo=UTC)
    await _add_usage_event(
        db,
        account_id=account.id,
        api_key_id=api_key.id,
        when=afternoon,
        cost_kop=888,
    )

    # Query with full ISO at noon — afternoon event excluded.
    r = await client.get(
        "/v1/usage?from=2026-04-29T00:00:00Z&to=2026-04-29T12:00:00Z",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["cost_kop"] == 0
    assert body["totals"]["request_count"] == 0


# ----- Multi-day window ------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_day_window_inclusive_both_ends(client, db):
    """``from=2026-04-27&to=2026-04-29`` covers events on day 27 morning,
    day 28 noon, day 29 evening — all three must be aggregated.
    """
    api_key, account, plaintext = await _seed_account_with_key(db)

    timestamps = [
        datetime(2026, 4, 27, 0, 0, 1, tzinfo=UTC),  # day 27 first second
        datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),  # day 28 noon
        datetime(2026, 4, 29, 23, 59, 0, tzinfo=UTC),  # day 29 late evening
    ]
    for ts in timestamps:
        await _add_usage_event(
            db, account_id=account.id, api_key_id=api_key.id, when=ts, cost_kop=10
        )

    # Boundary excluded: day 26 last second + day 30 first second.
    boundary_outside = [
        datetime(2026, 4, 26, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 4, 30, 0, 0, 1, tzinfo=UTC),
    ]
    for ts in boundary_outside:
        await _add_usage_event(
            db, account_id=account.id, api_key_id=api_key.id, when=ts, cost_kop=9999
        )

    r = await client.get(
        "/v1/usage?from=2026-04-27&to=2026-04-29",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    body = r.json()
    # 3 events × 10 kop = 30 kop. Outside-boundary events MUST be excluded.
    assert body["totals"]["cost_kop"] == 30, body
    assert body["totals"]["request_count"] == 3
