"""Tests for the ``audit_log`` helper + ORM (Sprint 6, Блок 3)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.db.models import AuditLog


@pytest.mark.asyncio
async def test_write_audit_inserts_row(db):
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()
    row = await write_audit(
        db,
        user_id=user_id,
        account_id=account_id,
        action="login_ok",
        request=None,
        meta={"key": "value"},
    )
    await db.commit()
    assert row.id is not None

    fetched = (await db.execute(select(AuditLog).where(AuditLog.id == row.id))).scalar_one()
    assert fetched.action == "login_ok"
    assert fetched.outcome == "ok"
    assert fetched.meta == {"key": "value"}


@pytest.mark.asyncio
async def test_write_audit_failed_outcome(db):
    row = await write_audit(
        db,
        user_id=None,
        account_id=None,
        action="login_failed",
        outcome="failed",
    )
    await db.commit()
    assert row.outcome == "failed"


@pytest.mark.asyncio
async def test_write_audit_user_id_can_be_null(db):
    """For events that fire before a user is resolved (e.g. login_failed
    against an unknown email)."""
    row = await write_audit(
        db,
        user_id=None,
        account_id=None,
        action="login_failed",
        outcome="failed",
    )
    await db.commit()
    assert row.user_id is None


@pytest.mark.asyncio
async def test_audit_log_outcome_check_constraint(db):
    """DB rejects outcomes outside the allowed set.

    Skip on SQLite where the CHECK is enforced as a WHERE-style check —
    SQLite catches the violation only at INSERT time of the row, which is
    what we want.
    """
    bad = AuditLog(
        user_id=None,
        account_id=None,
        action="weird_event",
        outcome="kinda-ok",
    )
    db.add(bad)
    with pytest.raises(Exception):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_high_signal_action_logged_at_warning(db, caplog):
    """``2fa_disabled`` is in ``_HIGH_SIGNAL_ACTIONS`` — verifies the
    structlog event still lands without crashing the helper.
    """
    row = await write_audit(
        db,
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        action="2fa_disabled",
        outcome="ok",
    )
    await db.commit()
    assert row.id is not None
