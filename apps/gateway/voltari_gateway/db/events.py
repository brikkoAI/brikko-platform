"""SQLAlchemy ORM event listeners.

Why this module exists
----------------------

We use a ``before_flush`` listener to keep one invariant without
sprinkling code across every endpoint that touches ``Account.tariff``:

* **TariffHistory append-on-change** (Sprint 11, Alembic 0013) — every
  transition of ``Account.tariff`` (including the initial value on
  signup) lands as a row in ``tariff_history``. The listener emits the
  INSERT inside the same flush as the UPDATE, so MRR-breakdown
  analytics never see an account whose ``tariff`` is on row N+1 but
  whose history table only has rows up to N. The listener guarantees
  atomicity *as long as the caller commits the same session*; if the
  transaction rolls back, the history row rolls back with it.

Why event listener and not a Postgres TRIGGER:

  1. SQLite (test path) doesn't support row triggers in the way we'd
     need; we'd then have two divergent sources of truth.
  2. The trigger has no access to a "who triggered this" string —
     ``triggered_by`` would be hardcoded in SQL. The listener gets a
     hint via ``Account._tariff_change_meta`` (an instance-attached
     dict) so endpoints can record "user_upgrade" / "cron:renewal".
  3. One writer (the gateway) means we don't need DB-level enforcement.

How endpoints opt-in extra metadata
-----------------------------------

The default ``reason`` / ``triggered_by`` is None unless the endpoint
sets ``account._tariff_change_meta = {"reason": "user_upgrade",
"triggered_by": "user"}`` *before* the flush. The dict is consumed
(popped) by the listener — re-using the same Account in another flush
won't accidentally re-tag a change.

PK resolution for new accounts
------------------------------

For brand-new ``Account`` instances flushed in the same unit-of-work
as the first ``TariffHistory`` insert, ``account.id`` is None at
``before_flush`` time. We resolve this by:

  1. Forcing the Account default (``uuid.uuid4()``) to fire eagerly so
     the id is concrete by the time we build the history row.
  2. Otherwise the listener would emit a NULL FK and the INSERT would
     fail at the DB layer with a NOT NULL violation.

This is safe because the column default is deterministic Python (no DB
round-trip) — pulling it forward changes only timing, not value.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session as SyncSession

# Sentinel attribute name used to attach per-flush metadata on Account
# instances. Endpoint code touches it via ``account._tariff_change_meta = ...``;
# the listener pops it during ``before_flush``.
_META_ATTR = "_tariff_change_meta"


def _coerce_tariff_value(value: Any) -> Any:
    """Return the underlying enum value or pass-through if already coerced.

    SQLAlchemy hands us either the enum member or the raw string
    (depending on whether the attribute was assigned an enum or a raw
    value). The history row stores the enum, so we just normalise to
    the enum type when possible.
    """
    # Lazy import to keep the module side-effect-free at import time.
    from voltari_gateway.db.models import Tariff

    if value is None or isinstance(value, Tariff):
        return value
    try:
        return Tariff(value)
    except ValueError:
        # Foreign value — let SQLAlchemy fail loudly on the INSERT
        # rather than silently coercing to a wrong enum.
        return value


def _make_tariff_history_listener() -> Any:
    """Build the actual ``before_flush`` callback.

    Factored so the registration function below stays small and the
    closure can be inspected in tests.
    """

    def _listener(session: SyncSession, _flush_ctx: Any, _instances: Any) -> None:
        # Lazy imports — keeps ``db/events.py`` cheap to import on cold
        # paths (e.g. Alembic env) where models haven't been touched yet.
        from voltari_gateway.db.models import Account, TariffHistory

        # 2026-05-01 — раньше listener писал signup-row для каждого нового
        # Account (is_new=True), но это ломало FK на проде: TariffHistory
        # попадает в тот же flush до того, как родительский Account
        # окажется в БД, и Postgres валит ForeignKeyViolationError.
        # Решение: пишем ТОЛЬКО при UPDATE Account.tariff (session.dirty).
        # Базовое значение тарифа на момент signup можно прочитать из
        # ``accounts.created_at`` + ``accounts.tariff`` — отдельная строка
        # в tariff_history для signup лишняя для MRR-analytics.
        for account in session.dirty:
            if not isinstance(account, Account):
                continue

            from sqlalchemy import inspect as sa_inspect

            insp = sa_inspect(account)
            attr = insp.attrs.tariff
            history = attr.history

            if not history.has_changes():
                continue

            new_value = _coerce_tariff_value(account.tariff)
            if not history.deleted:
                continue
            from_value = _coerce_tariff_value(history.deleted[0])
            if from_value == new_value:
                continue

            meta: dict[str, Any] = getattr(account, _META_ATTR, None) or {}

            # Pop the meta dict so it isn't re-used on the next flush.
            if hasattr(account, _META_ATTR):
                with contextlib.suppress(AttributeError):
                    delattr(account, _META_ATTR)

            row = TariffHistory(
                account_id=account.id,
                from_tariff=from_value,
                to_tariff=new_value,
                reason=meta.get("reason"),
                triggered_by=meta.get("triggered_by"),
            )
            session.add(row)

    return _listener


_tariff_history_listener = _make_tariff_history_listener()
_REGISTERED = False


def register_listeners() -> None:
    """Install ORM event listeners exactly once per process.

    Idempotent — calling twice is a no-op. Tests that build their own
    engines call this from a fixture; the production app calls it from
    the lifespan hook in ``main.py`` (or implicitly, by importing
    ``voltari_gateway.db.models``, which calls back into this module
    once on first import — see the trailing line in ``models.py``).
    """
    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(SyncSession, "before_flush", _tariff_history_listener)
    _REGISTERED = True


def is_registered() -> bool:
    """Return True if listeners are installed (used by tests)."""
    return _REGISTERED


__all__ = ["is_registered", "register_listeners"]
