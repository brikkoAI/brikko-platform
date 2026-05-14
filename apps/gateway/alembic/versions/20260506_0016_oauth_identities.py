"""OAuth identity table for Google + Yandex social login.

Revision ID: 0016_oauth_identities
Revises: 0015_oauth_clients_and_codes
Create Date: 2026-05-06

Adds ``oauth_identities`` — a minimal mapping (provider, subject) → user_id
used by ``POST /v1/auth/oauth/{provider}/callback`` to link a Google or
Yandex account to a Brikko user.

Why a separate table (and not a JSONB column on users)
------------------------------------------------------

* A single user MAY (in V2) have multiple identities — Google for the
  desktop laptop, Yandex for the work browser.
* The (provider, subject) tuple has a UNIQUE constraint that we need at
  the DB layer for race-safety (two parallel callbacks linking the same
  Google account to two different Brikko users would both succeed
  without it).
* JSONB fields can't be UNIQUE-indexed cheaply on portable SQL.

What we DO and DON'T store
--------------------------

We store **only** what we need at link time + for display:

* ``email_at_link`` / ``email_verified_at_link`` — useful for security
  audit ("did the provider tell us this email was verified at the
  moment we linked?"). NULL when the provider didn't return an email
  (e.g. Yandex without email scope grant).
* ``display_name`` / ``avatar_url`` — UX-only, refreshed on each login.
* ``linked_via`` — ``"signup"`` | ``"settings"`` | ``"email_match"``;
  drives the audit-log narrative.
* ``last_login_at`` — bumped on each successful callback. Not the
  display field (that's ``users.created_at``); used by ops to spot
  dormant identities.

We deliberately do NOT store:

* Provider raw profile JSON (152-ФЗ data minimisation).
* OAuth access/refresh tokens from the provider — we never call back
  into Google/Yandex APIs, so persisting a token would be a liability,
  not an asset.

The login flow itself is stateless (HMAC-signed state token in the
``Authorization`` redirect, verified on callback) so there is no
``oauth_logins`` / ``oauth_states`` table — Redis is not on the hot path.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from voltari_gateway.db.migration_helpers import (
    create_index_idempotent,
    create_table_idempotent,
    has_table,
)
from voltari_gateway.db.models import GUID

revision: str = "0016_oauth_identities"
down_revision: str | None = "0015_oauth_clients_and_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    create_table_idempotent(
        "oauth_identities",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "user_id",
            GUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_link", sa.String(length=255), nullable=True),
        sa.Column(
            "email_verified_at_link",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
        sa.Column("linked_via", sa.String(length=32), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "subject",
            name="uq_oauth_identities_provider_subject",
        ),
        sa.CheckConstraint(
            "provider IN ('google','yandex')",
            name="ck_oauth_identities_provider",
        ),
        sa.CheckConstraint(
            "linked_via IN ('signup','settings','email_match')",
            name="ck_oauth_identities_linked_via",
        ),
    )

    # Hot lookup paths:
    #  1) ``WHERE provider = ? AND subject = ?`` on every callback.
    #     The UNIQUE constraint already gives us this index, but PG names
    #     it after the constraint — keeping a named index makes EXPLAIN
    #     output friendlier.
    #  2) ``WHERE user_id = ?`` for the /v1/account/oauth/identities
    #     listing on the security settings page.
    create_index_idempotent(
        "ix_oauth_identities_user_id",
        "oauth_identities",
        ["user_id"],
    )


def downgrade() -> None:
    if has_table("oauth_identities"):
        op.drop_index(
            "ix_oauth_identities_user_id",
            table_name="oauth_identities",
            if_exists=True,
        )
        op.drop_table("oauth_identities")
