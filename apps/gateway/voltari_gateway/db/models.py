"""SQLAlchemy 2.0 ORM models for the Voltari gateway.

Schema mirrors `02_Product/04_tech_stack.md` §3 with simplifications for the
MVP scope — we keep what the API and auth layer need today and leave admin /
billing / subscriptions / documents tables for separate migrations.

All monetary values are stored in **kopecks** as ``BigInteger``. UUIDs are
stored portably (PostgreSQL UUID, otherwise CHAR(36) string).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from datetime import date as _date
from decimal import Decimal
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, JSON, TypeDecorator

from voltari_gateway.billing.constants import (
    REFUND_OVERDRAFT_FLOOR_KOPECKS,
    TRANSACTION_AMOUNT_SIGN_CONSTRAINT,
)

# ---------- portable UUID type --------------------------------------------------


class GUID(TypeDecorator[uuid.UUID]):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID, falls back to CHAR(36) on SQLite (tests).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# JSON column that uses JSONB on Postgres, JSON elsewhere
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _enum_col(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """SQLAlchemy Enum that stores ``Enum.value`` strings (e.g. "active") instead
    of member names ("ACTIVE"). Keeps DB rows portable and predictable in SQL.
    """
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
    )


# ---------- enums --------------------------------------------------------------


class Tariff(enum.StrEnum):
    PAYG = "payg"
    PRO = "pro"
    # Sprint 6 — отдельный тариф с PII-маскингом включённым (proxy-обфускация
    # ФИО/email/телефонов перед отправкой в иностранные провайдеры). Цена
    # выше Pro на 800 ₽/мес; зафиксировано в финмодели M3.
    PRO_PRIVACY = "pro_privacy"
    TEAM = "team"
    BUSINESS = "business"
    BUSINESS_PLUS = "business_plus"


class AccountStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class SeatRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class ApiKeyScope(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class ApiKeyStatus(enum.StrEnum):
    ACTIVE = "active"
    ROTATING = "rotating"
    REVOKED = "revoked"


# Sprint MCP S1 (CEO 2026-05-11) — MCP token surface.
# Sprint MCP S3 (CEO 2026-05-12) — extended with four read-only catalog
# scopes (list_models, read_traces, list_cookbook, list_integrations) +
# wildcard ``all`` scope. Wildcard exists because the default onboarding
# token in the helper-skill (``brikko-helper init``) needs to call any of
# the seven tools without forcing the user to pick a scope upfront. The
# single-scope-per-token contract holds for **restrictive** tokens (which
# an admin/customer can mint via /v1/mcp/tokens with a specific scope).
#
# MCP tools have a deliberately narrow surface. Seven scopes cover the
# CEO/dev/agency use-cases over Claude Desktop / Cursor / Continue / Zed:
#
#   - read_account       : балансы, тариф, статус — Cursor MCP UI shows it.
#   - read_usage         : usage events / стоимость / распределение по моделям.
#   - recommend_model    : router.route_request (dry-run) — "give me the right
#                          model for this task".
#   - list_models        : публичный каталог моделей с RUB-ценами (S3).
#   - read_traces        : последние N traces из gateway_request_log (S3).
#   - list_cookbook      : 5 готовых рецептов (S3).
#   - list_integrations  : Cursor/Cline/Claude Code tool-guides (S3).
#   - all                : super-scope — проходит scope-check для всех tools.
#                          Используется default-токеном из helper-skill (S3).
#
# Adding a new scope = enum change + alembic migration that recreates the
# CHECK constraint (Postgres) / table (SQLite).
class McpScope(enum.StrEnum):
    READ_ACCOUNT = "read_account"
    READ_USAGE = "read_usage"
    RECOMMEND_MODEL = "recommend_model"
    LIST_MODELS = "list_models"
    READ_TRACES = "read_traces"
    LIST_COOKBOOK = "list_cookbook"
    LIST_INTEGRATIONS = "list_integrations"
    ALL = "all"


class McpTokenStatus(enum.StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class TransactionKind(enum.StrEnum):
    TOPUP = "topup"
    CHARGE = "charge"
    REFUND = "refund"
    SUBSCRIPTION = "subscription"
    AUTOREFILL = "autorefill"


# ---------- base ---------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base. ORM-only, no business logic here."""

    type_annotation_map: ClassVar[dict[type, Any]] = {
        dict[str, Any]: JSONType,
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# ---------- tables -------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Auth foundation (Alembic 0003) ---
    # Single-use, hashed tokens. Plaintext is delivered only by email.
    verification_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_reset_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Telegram link (Alembic 0010, Sprint 4 Поток M) ---
    # Linked Telegram chat_id (private chat) for @VoltariBot. NULL = not
    # linked. BIGINT because TG can issue group chat_ids beyond INT32.
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    # --- TOTP / 2FA (Sprint 6, Alembic 0011) ---
    # Encrypted TOTP secret (Fernet AES-128). NULL = not configured.
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Becomes True only after the user verified the first 6-digit code.
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # JSON list of bcrypt-hashed recovery codes ([] when consumed; NULL when not set).
    totp_recovery_codes_hashed: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    seats: Mapped[list[Seat]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    balance_kopecks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tariff: Mapped[Tariff] = mapped_column(
        _enum_col(Tariff, "tariff_enum"), default=Tariff.PAYG, nullable=False
    )
    status: Mapped[AccountStatus] = mapped_column(
        _enum_col(AccountStatus, "account_status_enum"),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    # Per CEO 29.04: "храним всегда" (вариант C) с opt-out для клиента.
    store_prompts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Sprint 4 Поток M — 152-ФЗ PII-маскинг proxy (alembic 0009).
    # Когда True, gateway перед отправкой prompt'а в иностранный провайдер
    # маскирует ФИО / email / телефон / паспорт / ИНН / СНИЛС / карты на
    # placeholder'ы (<NAME_1>, <PHONE_2>, ...). Mapping хранится в Redis 1ч,
    # после ответа провайдера — раз-маскируется. Также включается per-request
    # через header ``X-PII-Protect: true`` или поле ``pii_protect: true``.
    pii_masking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Studio onboarding flag (Alembic 0015) ---
    # True iff at least one OAuth code was issued for this account via the
    # ``studio`` first-party client. Used by analytics + by the welcome
    # credit guard (idempotent grant key). NOT a tariff modifier; we keep
    # ``Tariff.PRO_PRIVACY`` as the actual entitlement gate.
    is_studio_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Free-form per-account settings JSONB (Alembic 0004) ---
    # Schema is intentionally open: notifications, locale preferences, dashboard
    # widgets etc. We never query into it from SQL — read/write happens via the
    # PATCH /v1/account/settings endpoint which validates the shape.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    # --- Tariff lifecycle (Alembic 0011) ---
    # When the currently active paid period ends (NULL on PAYG and on plans
    # never charged). Set to (charge_at + 30d) on tariff upgrade. Cron
    # auto-renewal — Sprint 7. Used by the dashboard ("осталось N дней").
    tariff_active_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Account closure (Sprint 6, Alembic 0011, 152-ФЗ ст. 21) ---
    # Soft delete with 30-day grace; cron purge runs out-of-band.
    closure_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closure_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    closure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Hard purged_at — when PII was actually wiped. After this point the
    # account row is a tombstone for compliance (audit_log keeps user_id).
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sprint 7 — set by the PII purge cron 1y after ``closed_at``. After
    # this timestamp the row is a financial tombstone (Transaction history
    # kept for 5y per 152-ФЗ) but personally-identifying fields on User /
    # Account have been anonymised. NULL means PII still intact (account
    # is either active or in grace).
    pii_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Routing preferences (Sprint 7, Alembic 0012, spec v1.5/22) ---
    # Per-account routing policy. Defaults preserve pre-Sprint-7 behaviour
    # (mode=smart + strategy=cheap == DEFAULT_AUTO_STRATEGY=CHEAP).
    # CEO 30.04 §10 #6: keep 'cheap' as the new-account default to avoid
    # an unannounced 30-50% COGS jump on PAYG.
    routing_mode: Mapped[str] = mapped_column(String(16), default="smart", nullable=False)
    routing_strategy: Mapped[str] = mapped_column(String(16), default="cheap", nullable=False)
    # JSON list of provider strings (e.g. ["openai","anthropic"]) when
    # strategy=='custom'. NULL = no whitelist (all providers allowed).
    routing_allowed_providers: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    # JSON list of model ids (catalogue id) when strategy=='custom'.
    # NULL = all models from allowed_providers.
    routing_allowed_models: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    routing_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Smart Router v2 per-account opt-in (Sprint S1, Alembic 0022) ---
    # Per CEO 2026-05-13 (design-doc Q7): the v2 pipeline rolls out
    # behind a per-account admin flip. Env var
    # ``SMART_ROUTER_V2_ENABLED`` is the global kill-switch; this column
    # is the per-account opt-in. ``should_use_pipeline()`` returns True
    # if either is set. Default False — new accounts use v1 until opted in.
    smart_router_v2_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )

    # --- Autorefill (Alembic 0002) ---
    autorefill_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ЮKassa saved-card handle returned by save_payment_method=true on first topup.
    autorefill_pm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    autorefill_threshold_kopecks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    autorefill_topup_kopecks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Subscription state (Sprint pay-per-use → subscription pivot, 2026-05-15) ---
    # CEO 2026-05-15: PAYG = welcome credits only (200 ₽), top-ups больше нет.
    # Единственный путь к paid usage — подписка Pro (290 ₽/мес) или Team
    # (1490 ₽/мес). Этот блок колонок ортогонален ``tariff`` (legacy PAYG/
    # PRO_PRIVACY и пр.) — он управляет именно subscription-биллингом, а не
    # entitlement-флагами как pii_masking_enabled. См. BRIEF_v2_pivot.md.
    #
    # Жизненный цикл:
    #   tier='payg'   active_until=NULL  canceled_at=NULL   ← новый аккаунт
    #   tier='pro'    active_until=now+30d  canceled_at=NULL ← оплачено
    #   tier='pro'    active_until=now+15d  canceled_at=set  ← cancel'нул, доедает
    #   tier='payg'   active_until=NULL  canceled_at=NULL   ← cron заэкспайрил
    subscription_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="payg", server_default="payg"
    )
    # NULL = нет активной подписки (только welcome-кредиты доступны).
    # datetime = paid until this moment (cron Phase 2 будет charge'ить заново).
    subscription_active_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Заполняется при cancel. Сама подписка остаётся активной до
    # ``subscription_active_until`` — юзер уже заплатил за период.
    subscription_canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Subscription renewal / dunning (Phase 2, Alembic 0025) ---
    # Number of *consecutive* failed renewal-charge attempts. Reset to 0 on
    # a successful renewal. After ``MAX_RENEWAL_RETRIES`` (3 in
    # ``subscription_renewal.py``) the cron downgrades tier → 'payg' and
    # emails the user with a re-link-card CTA. Stays 0 for the steady-state
    # success case (no SQL writes on the happy path).
    renewal_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Timestamp of the most recent failed renewal attempt; drives the
    # Stripe-style retry schedule (T+24h / T+72h after this stamp). NULL
    # whenever ``renewal_retry_count == 0``.
    renewal_last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Acquisition / UTM (Sprint 11, Alembic 0013) ---
    # Free-form acquisition channel ("habr", "vc", "tg_org", "direct", ...).
    # Frontend resolves cookies → channel → posts in the signup body.
    # All four columns are nullable: legacy rows (created before 0013)
    # don't have UTM and the activation funnel must accept them as
    # ``acquisition_channel IS NULL`` ("unknown" bucket in the dashboard).
    acquisition_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(64), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(128), nullable=True)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    seats: Mapped[list[Seat]] = relationship(back_populates="account", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    # Sprint MCP S1 — Brikko-MCP tokens, surface'd via api.brikko.ru/mcp.
    # Separate table from api_keys: different scopes, different tariff
    # limits, different audit-event labels. See models.McpToken comment.
    mcp_tokens: Mapped[list[McpToken]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Allow a tiny overdraft window (gateway can write off the last token call
        # without aborting the request mid-flight). Hard-stop is enforced in code.
        # Floor is defined as a constant in ``billing.constants`` so the engine
        # logic and the constraint can never silently disagree (BE P0-16).
        CheckConstraint(
            f"balance_kopecks >= {REFUND_OVERDRAFT_FLOOR_KOPECKS}",
            name="balance_check",
        ),
        Index("ix_accounts_owner_id", "owner_id"),
        Index("ix_accounts_acquisition_channel", "acquisition_channel"),
    )


class Seat(Base, TimestampMixin):
    __tablename__ = "seats"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[SeatRole] = mapped_column(_enum_col(SeatRole, "seat_role_enum"), nullable=False)

    account: Mapped[Account] = relationship(back_populates="seats")
    user: Mapped[User] = relationship(back_populates="seats")

    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_seats_account_user"),
        Index("ix_seats_account_id", "account_id"),
        Index("ix_seats_user_id", "user_id"),
    )


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # First 14 chars for UI display: "sk-vlt-aB12cd34..." — also used as fast lookup index
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope: Mapped[ApiKeyScope] = mapped_column(
        _enum_col(ApiKeyScope, "api_key_scope_enum"), default=ApiKeyScope.WRITE, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # TD-009 (Sprint 3 Поток H) — opt-in expiry. NULL = never expires
    # (backwards-compat for keys created before Alembic 0008). When set,
    # ``require_api_key`` rejects requests after ``expires_at`` with a
    # 401 ``invalid_api_key`` (code=``api_key_expired``).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ApiKeyStatus] = mapped_column(
        _enum_col(ApiKeyStatus, "api_key_status_enum"),
        default=ApiKeyStatus.ACTIVE,
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="api_keys")

    __table_args__ = (
        Index("ix_api_keys_account_id", "account_id"),
        Index("ix_api_keys_status", "status"),
    )


class McpToken(Base, TimestampMixin):
    """Brikko-MCP tokens — separate surface from ``api_keys``.

    Sprint MCP S1 (Alembic 0019). Plaintext literal is ``mcp-brk-<22 b62>``.
    Argon2id hash + 14-char prefix lookup mirror ``ApiKey``. We keep this as
    a separate table (not a polymorphic flag on ``api_keys``) for three
    reasons:

      1. Different surface contracts. MCP tools never reach
         ``/v1/chat/completions``; mixing the scope enum was inviting bugs
         the day someone added "mcp_read_account" to ApiKeyScope.
      2. Different tariff caps (S1 ships generous limits — PAYG=5, PRO=15,
         TEAM=30 — much higher than chat keys because Claude Desktop / Cursor
         are stateless per-machine).
      3. Different audit labels in the activity feed
         (``mcp_token_created`` vs ``api_key_created``) so the user can
         see at a glance which surface a credential was issued for.

    Soft-revoke pattern: ``status=REVOKED`` + ``revoked_at=now()``. We do
    NOT hard-delete; usage events (when MCP usage tracking lands in S5) may
    foreign-key to this row.
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # First 14 chars: "mcp-brk-aB12cd". Indexed for cheap bearer lookup
    # before the expensive argon2 verify (S2 will wire this up).
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope: Mapped[McpScope] = mapped_column(
        _enum_col(McpScope, "mcp_scope_enum"),
        default=McpScope.READ_ACCOUNT,
        nullable=False,
    )
    status: Mapped[McpTokenStatus] = mapped_column(
        _enum_col(McpTokenStatus, "mcp_token_status_enum"),
        default=McpTokenStatus.ACTIVE,
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Same opt-in expiry contract as ApiKey.expires_at — NULL = forever.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="mcp_tokens")

    __table_args__ = (
        Index("ix_mcp_tokens_account_id", "account_id"),
        Index("ix_mcp_tokens_status", "status"),
    )


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[TransactionKind] = mapped_column(
        _enum_col(TransactionKind, "transaction_kind_enum"), nullable=False
    )
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        # Sign convention (BE P0-2): one swapped sign in `engine.py` was a
        # ledger-level catastrophe waiting to happen — a refund posted as
        # +amount instead of -amount would silently double the balance. The
        # CHECK below mirrors the sign rules in
        # `voltari_gateway/billing/engine.py` so the database refuses bad
        # rows even if Python forgets.
        #
        #   topup / subscription / autorefill → strictly > 0
        #   charge                            → strictly < 0
        #   refund                            → != 0 (either direction)
        CheckConstraint(
            "(type IN ('topup','subscription','autorefill') AND amount_kopecks > 0) "
            "OR (type = 'charge' AND amount_kopecks < 0) "
            "OR (type = 'refund' AND amount_kopecks != 0)",
            name=TRANSACTION_AMOUNT_SIGN_CONSTRAINT,
        ),
        # Idempotency on (account_id, ref_id): credit/debit short-circuit on
        # this UNIQUE so a duplicate webhook delivery cannot post the same
        # ledger row twice. Mirrors the partial unique index created by
        # alembic 0002 for Postgres (``WHERE ref_id IS NOT NULL``); on
        # SQLite we accept the strict version (rows with NULL ref_id are
        # internal book-keeping).
        UniqueConstraint("account_id", "ref_id", name="uq_transactions_account_ref"),
        Index("ix_transactions_account_id", "account_id"),
        Index("ix_transactions_account_created", "account_id", "created_at"),
    )


class AccountHold(Base):
    """Pre-flight balance reservation, kept transactionally with the balance.

    Migrated from Redis (Alembic 0005) to remove the ``balance(PG) +
    sum_holds(Redis)`` race that let two concurrent requests both pass the
    pre-flight check. The flow is now strictly:

        BEGIN;
          SELECT balance FROM accounts FOR UPDATE;
          SELECT COALESCE(SUM(amount_kopecks), 0) FROM account_holds
            WHERE account_id = :id;
          INSERT INTO account_holds (...) ON CONFLICT (account_id, ref_id) DO NOTHING;
        COMMIT;

    A hold is freed by ``DELETE`` (release on error, or commit_hold path).
    ``expires_at`` only matters for the janitor cron — we never trust the
    timestamp at read time; deleting an expired-but-not-collected row is
    fine because committed transactions land via the normal debit flow.
    """

    __tablename__ = "account_holds"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    ref_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("amount_kopecks > 0", name="ck_account_holds_amount_positive"),
        UniqueConstraint("account_id", "ref_id", name="uq_account_holds_account_ref"),
        Index("ix_account_holds_account_id", "account_id"),
        Index("ix_account_holds_expires_at", "expires_at"),
    )


class UsageEvent(Base, TimestampMixin):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # Sprint 11 — denormalised provider tag (Alembic 0013). The gateway
    # already knows the upstream provider at write time
    # (``ModelSpec.provider``); persisting it sidesteps the brittle
    # ``WHEN model LIKE 'gpt-%' THEN 'openai'`` CASE the analytics SQL
    # used to carry. Backfilled by the migration; new rows must populate.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Sprint M1 — modality + billable unit (Alembic 0014).
    # ``modality`` ∈ {"chat", "stt", "embeddings"}; "chat" is the
    # backwards-compat default for every row created before STT/embeddings
    # endpoints landed. ``unit`` ∈ {"token", "minute"} — the unit the row
    # is billed in. Token-counted rows still write input/output_tokens;
    # minute-counted rows put 0 in those and stash the duration in
    # ``input_tokens`` *only* if the analytics layer asks for it (today
    # they don't — cost_kopecks is the truth).
    modality: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="token", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_kopecks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    __table_args__ = (
        Index("ix_usage_events_account_id", "account_id"),
        Index("ix_usage_events_api_key_id", "api_key_id"),
        Index("ix_usage_events_account_created", "account_id", "created_at"),
        Index("ix_usage_events_provider_created", "provider", "created_at"),
        Index("ix_usage_events_modality_created", "modality", "created_at"),
    )


class RequestPayload(Base, TimestampMixin):
    """Encrypted request/response body, retained per `account.store_prompts` flag.

    CEO 29.04: store-by-default with at-rest encryption. Clients can opt out
    via `accounts.store_prompts = false`. TTL controlled by
    `REQUEST_PAYLOAD_RETENTION_DAYS` env var; a cron job (not in this skeleton)
    truncates rows past `retention_until`.
    """

    __tablename__ = "request_payloads"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    usage_event_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    encrypted_prompt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encrypted_response: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    opt_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_request_payloads_retention", "retention_until"),)


class ProcessedWebhook(Base):
    """Idempotency + DLQ log for webhooks (BE P0-6).

    Fulfills two roles:

    * **Idempotency** — replay protection on the (source, payment_id) key.
      ЮKassa retries 5xx aggressively; without a record of "we've seen
      this payment_id" two concurrent retries could each pass the
      ``credit_account`` idempotency check (which itself locks on
      ``transactions.ref_id``) and one would race the other to take the
      account row lock.
    * **DLQ** — when a webhook is well-formed and signature-valid but the
      handler can't process it (account doesn't exist, amount=0,
      database transient failure), we record the failure with
      ``status='failed'`` and ``error_message`` so an operator can find
      the row in the dashboard and act. The webhook handler returns
      500 in that case so ЮKassa retries; on retry the row is updated
      to ``processed`` if the underlying issue resolved.

    Fixed in BE P0-22: the migration created this table in 0002 but the
    ORM never declared it, so ``Base.metadata.create_all()`` (used in
    unit tests) didn't have it and the schema-drift integration test
    correctly flagged the orphan.
    """

    __tablename__ = "processed_webhooks"

    payment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="yookassa")

    # --- Status + DLQ (Alembic 0007) ---
    # Stored as a free-form String so a future status (e.g. ``manual``)
    # doesn't need a schema migration. CHECK constraint pins valid values.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processed")
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('processed','failed')",
            name="ck_processed_webhooks_status",
        ),
        Index("ix_processed_webhooks_first_seen", "first_seen_at"),
        Index("ix_processed_webhooks_status", "status"),
    )


class WelcomeCreditsLog(Base):
    """Anti-abuse log for the 200 ₽ signup bonus.

    PK is ``email_hash`` (SHA-256 of normalised email) so we don't keep PII
    in plaintext yet still prevent re-grant via ``delete + signup`` with the
    same email. ``ip_hash`` is for forensics, not for blocking.
    """

    __tablename__ = "welcome_credits_log"

    email_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_welcome_credits_granted_at", "granted_at"),)


class SubscriptionReminderSent(Base):
    """Idempotency log for subscription lifecycle emails (Phase 2, Alembic 0025).

    Without this table, two cron ticks in the same day (crash-restart of the
    container, two replicas racing, op-team manual re-run) would email the
    customer twice about the same renewal. We persist a row per
    ``(account_id, reminder_type, period_date)`` and rely on the UNIQUE to
    short-circuit duplicates — the cron catches ``IntegrityError`` and
    treats it as "already sent".

    Period anchor is the *date* of ``subscription_active_until`` for the
    period in question, not a timestamp. ``active_until`` is supposed to be
    a stable datetime, but bypassing minute-level jitter (clock skew between
    replicas, NTP adjustments) keeps the idempotency key robust.

    Reminder types (extend over time, never reuse a value):
      * ``renewal_t_minus_3``    — T-3d "we'll charge ₽X in 3 days"
      * ``renewal_failed``       — "charge attempt N failed, retrying in 24h"
      * ``renewal_downgraded``   — "3 retries exhausted; tier→payg"
    """

    __tablename__ = "subscription_reminders_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    reminder_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_date: Mapped[_date] = mapped_column(sa.Date(), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "reminder_type",
            "period_date",
            name="uq_subscription_reminders_sent",
        ),
        Index(
            "ix_subscription_reminders_sent_account",
            "account_id",
            "period_date",
        ),
    )


class EmailInvite(Base):
    """Pending workspace invite emitted to an external email.

    Flow:
    1. Owner POSTs an invite → row created with TTL (typically 7 days).
       Plaintext token is emailed; only ``token_hash`` is persisted.
    2. Recipient signs up + accepts → row marked ``accepted_at``, a
       ``Seat(account=..., user=..., role=role)`` is added.
    """

    __tablename__ = "email_invites"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin','member')", name="ck_email_invites_role"),
        Index("ix_email_invites_account_id", "account_id"),
        Index("ix_email_invites_email", "email"),
        Index("ix_email_invites_token_hash", "token_hash", unique=True),
    )


class Session(Base):
    """Mirror of Redis refresh-token whitelist for the dashboard UI.

    Postgres is **not** the source of truth — the live whitelist lives in
    Redis (TTL = refresh JWT lifetime). This table exists so:

    * The Sessions API (``GET /v1/auth/sessions``) can render device /
      location columns without bloating every JTI key in Redis.
    * Audit log can reference ``session.id`` instead of the raw JTI
      (which leaks key material to anyone with read access on logs).

    Rows are inserted at login/refresh, updated on each refresh
    rotation (``last_seen_at`` + new JTI), and tombstoned on revoke
    (``revoked_at`` set, row kept for the audit trail). The cron does
    NOT purge revoked rows — they stay until the user account is
    purged. Active rows are the ones with ``revoked_at IS NULL``.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_jti: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("user_id", "refresh_jti", name="uq_sessions_user_jti"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_refresh_jti", "refresh_jti"),
        Index("ix_sessions_revoked_at", "revoked_at"),
    )


class DataExport(Base):
    """Per-user data export request (152-ФЗ ст. 14, GDPR Art. 20).

    Lifecycle: ``pending`` → ``processing`` → ``ready`` → (``downloaded``
    optional) → ``expired`` (after ``expires_at``) → ``purged`` (S3 file
    deleted by cron).

    The actual ZIP generation runs in a background worker (added Sprint 7).
    For Sprint 6 the row is created on POST and the ``ready`` status is
    set inline by a stub generator that produces a ``data:`` URL — the
    real S3 upload comes later. Schema is forward-compatible.
    """

    __tablename__ = "data_exports"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    download_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','ready','expired','failed','purged')",
            name="ck_data_exports_status",
        ),
        Index("ix_data_exports_user_id", "user_id"),
        Index("ix_data_exports_account_id", "account_id"),
        Index("ix_data_exports_expires_at", "expires_at"),
    )


class AuditLog(Base):
    """Append-only ledger of security-relevant events.

    Single source of truth for: login (ok/failed), 2FA toggle, password
    change, session revoke, tariff upgrade, account closure, data export,
    API key rotation. Read by the dashboard «Активность аккаунта» tab and
    by ops dashboards (Sentry-side filtering).

    Retention: 90 days hot in PG; older rows are archived out by Sprint 7
    cron. Rows survive user/account purge — FKs are SET NULL, not CASCADE.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ok','denied','failed')",
            name="ck_audit_log_outcome",
        ),
        Index("ix_audit_log_user_id", "user_id"),
        Index("ix_audit_log_account_id", "account_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_user_created", "user_id", "created_at"),
    )


class TariffHistory(Base):
    """Append-only record of every tariff transition on an account.

    Sprint 11 — Alembic 0013. Why a separate table instead of writing
    ``tariff_changed`` rows into ``audit_log``:

    * Audit log is security-flavoured: rows have meta-JSON, no FK
      coupling to ``Tariff`` enum values, retention is 90 days.
    * MRR-breakdown SQL needs to compute "what tariff was account X on
      at timestamp T" cheaply — that's a window function over
      ``tariff_history`` indexed on ``(account_id, changed_at DESC)``,
      not a JSON-blob scan.
    * Retention is forever (or until account is purged) for accurate
      historic MRR; tying that to audit_log forces a 5-year audit
      retention we don't otherwise want.

    Rows are written by the SQLAlchemy event listener in
    ``voltari_gateway.db.events`` whenever ``Account.tariff`` changes
    (including the initial value on signup, where ``from_tariff`` is
    NULL). The listener runs ``before_flush`` so the INSERT lands in
    the same transaction as the ``UPDATE accounts SET tariff = ...``.
    """

    __tablename__ = "tariff_history"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # NULL on the very first row for an account (creation). Otherwise the
    # tariff value the row replaces.
    from_tariff: Mapped[Tariff | None] = mapped_column(
        _enum_col(Tariff, "tariff_enum"), nullable=True
    )
    to_tariff: Mapped[Tariff] = mapped_column(_enum_col(Tariff, "tariff_enum"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    # Why the change happened — free-form, but the conventions are:
    #   "signup"            — first row (from_tariff IS NULL).
    #   "user_upgrade"      — owner-driven via PATCH /v1/account/tariff.
    #   "user_downgrade"    — same path, going down.
    #   "cron:renewal"      — Sprint 7 auto-renewal.
    #   "cron:closure"      — account closure cron flips tariff to PAYG.
    #   "admin"             — manual override via management API.
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_tariff_history_account_changed", "account_id", "changed_at"),)


class OAuthClient(Base, TimestampMixin):
    """First-party (and future third-party) OAuth client registry.

    ``client_id`` is the public identifier the client app presents in
    ``/v1/oauth/authorize?client_id=…``. We deliberately use a string PK
    (e.g. ``"studio"``, ``"studio-mobile"``) rather than a UUID so the
    URL stays human-readable.

    PKCE substitutes for ``client_secret`` — first-party Brikko clients
    are public clients in the OAuth sense (they ship in user-controlled
    binaries, can't keep a secret). ``redirect_uris`` is an exact-match
    allow-list to defend against open-redirect.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    allowed_scopes: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    is_first_party: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class OAuthIdentity(Base, TimestampMixin):
    """Mapping from (provider, subject) to a Brikko user.

    A single user MAY (in V2) have multiple identities — one per
    provider — so we keep this in its own table rather than columns on
    ``users``. The UNIQUE on (provider, subject) at the DB layer gives
    us race-safety against two parallel callbacks racing to link the
    same external account to different Brikko users.

    What we store is intentionally minimal (152-ФЗ data minimisation):
    we keep the bare facts needed at link time + for display, and never
    persist the provider's raw profile JSON or the provider's
    access/refresh tokens. The Brikko side never calls back into the
    provider APIs after the userinfo fetch — there is nothing to
    refresh.
    """

    __tablename__ = "oauth_identities"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ``google`` | ``yandex`` (extend in V2 — Apple/Microsoft etc).
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provider's stable user identifier (Google ``sub``, Yandex ``id``).
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Email at the time of link. NULL when the provider returned no email
    # (e.g. Yandex login without email scope). Display-only — we still
    # use ``users.email`` as the canonical identity.
    email_at_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Whether the provider declared the email verified at link time.
    # Drives the auto-link / manual-link decision in the callback flow:
    # only provider-verified emails auto-link to an existing user.
    email_verified_at_link: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # ``signup`` | ``settings`` | ``email_match`` — narrative for the
    # audit log + dashboard display ("connected via signup on 2026-05-06").
    linked_via: Mapped[str] = mapped_column(String(32), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_oauth_identities_provider_subject"),
        CheckConstraint(
            "provider IN ('google','yandex')",
            name="ck_oauth_identities_provider",
        ),
        CheckConstraint(
            "linked_via IN ('signup','settings','email_match')",
            name="ck_oauth_identities_linked_via",
        ),
    )


class OAuthAuthorizationCode(Base):
    """Single-use authorization code minted by ``GET/POST /v1/oauth/authorize``.

    Plaintext code is the URL-safe random token returned to the client in
    the redirect; only its SHA-256 hash is stored here. ``used_at`` is set
    inside the same transaction that mints the access token, so a replay
    of the same code is a NO-OP (the second transaction reads
    ``used_at IS NOT NULL`` and bails with ``invalid_grant``).
    """

    __tablename__ = "oauth_authorization_codes"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    scopes: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "code_challenge_method = 'S256'",
            name="ck_oauth_codes_pkce_method_s256",
        ),
        Index("ix_oauth_codes_expires_at", "expires_at"),
        Index("ix_oauth_codes_user_account", "user_id", "account_id"),
    )


class GatewayRequestLog(Base):
    """Per-request observability log (BrikkoLens — Phase 5 #4, 2026-05-09).

    Каждый запрос к /v1/chat/completions / /v1/messages / etc. пишется
    сюда (insert после получения ответа провайдера, не блочит SSE).
    Frontend `/app/traces` — главный потребитель.

    Различие от ``UsageEvent`` (она тоже хранит часть полей):
    * UsageEvent — billing-shaped, агрегируется в Transaction; retention
      постоянная (для финансовой отчётности).
    * GatewayRequestLog — observability-shaped, retention 8 недель + cold
      archive; содержит latency/error/streaming/tool flags + full
      model_params + opt-in body snapshots.

    Партиционирование (RANGE по weeks) отложено до M6 — research §4.2
    показал что Postgres держит до ~5M req/мес без партиций; добавим
    миграцию-рефакторинг когда подойдём.
    """

    __tablename__ = "gateway_request_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    # routing
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    routed_from: Mapped[str | None] = mapped_column(Text, nullable=True)

    # timings
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # usage
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # total_tokens — GENERATED column в БД (sum of prompt+completion).
    # Декларируем в ORM как read-only int — иначе test_migrations_roundtrip
    # ругается «DB has columns the ORM does not declare».  INSERT/UPDATE
    # вручную в эту колонку упадут на DB level — БД сама всё считает.
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        Computed("prompt_tokens + completion_tokens", persisted=True),
        nullable=False,
    )

    # cost
    cost_kop: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fx_usd_rub: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    # status / error
    status: Mapped[str] = mapped_column(Text, nullable=False)
    http_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # flags
    is_streaming: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pii_masked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tools_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # bodies (opt-in via Account.store_prompts)
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    # custom user metadata + model params
    model_params: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        Index("idx_gwrl_request_id", "account_id", "request_id"),
        Index("idx_gwrl_api_key_recent", "api_key_id", "created_at"),
        Index("idx_gwrl_model_recent", "account_id", "model", "created_at"),
        Index("idx_gwrl_account_recent", "account_id", "created_at"),
    )


class ProviderBalance(Base, TimestampMixin):
    """Last known balance on each upstream LLM provider account.

    Sprint 14 (2026-05-10) — admin /v1/account/admin/provider_balances.

    One row per provider (UNIQUE on ``provider``). Refreshed by:
      * ``api`` adapter — DeepSeek / Sber / Moonshot. Public balance API.
      * ``manual``      — POST /…/{provider}/manual. OpenAI/Anthropic/Together
                          /MiniMax/Zhipu/Yandex/Google — no public balance API
                          (Phase 1) or requires Cloud Billing IAM (Phase 2).
      * ``scrape``      — Playwright (Phase 2 — отдельный Docker сервис).

    ``balance_rub_kopecks`` — нормализация под единый ₽ для runway-расчёта.
    NULL когда currency='tokens' (Sber GigaChat — токены, не валюта).

    ``burn_rate_kopecks_per_day`` / ``runway_days`` — computed-at-refresh
    из ``usage_events.cost_kopecks`` за последние 7 дней. Хранятся в
    строке чтобы admin endpoint отдавал snapshot одним SELECT.

    ``raw_response`` — последний raw JSON от API провайдера. Помогает
    при дебаге смены формата ответа.
    """

    __tablename__ = "provider_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    balance_native: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    balance_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    balance_rub_kopecks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free-form String + CHECK constraint pattern (same as ProcessedWebhook.status):
    # позволяет добавить новый статус без миграции схемы.
    fetch_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    fetch_method: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    burn_rate_kopecks_per_day: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    runway_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", name="uq_provider_balances_provider"),
        CheckConstraint(
            "fetch_status IN ('ok','error','manual','pending')",
            name="ck_provider_balances_fetch_status",
        ),
        CheckConstraint(
            "fetch_method IN ('api','manual','scrape')",
            name="ck_provider_balances_fetch_method",
        ),
        Index("ix_provider_balances_last_fetched_at", "last_fetched_at"),
    )


class ProviderCookies(Base, TimestampMixin):
    """Metadata for Playwright scraper cookie files (Sprint 14 Phase 2).

    Сами cookies хранятся на shared-volume в зашифрованном виде (Fernet,
    тот же ENCRYPTION_KEY что и для request_payloads). Эта таблица —
    только метаданные: provider → cookie_path + upload audit trail.

    См. ``apps/scraper/`` (Playwright service) и
    ``voltari_gateway/api/admin_cookies.py`` (upload endpoint).
    """

    __tablename__ = "provider_cookies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    cookie_path: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", name="uq_provider_cookies_provider"),
        Index("ix_provider_cookies_uploaded_at", "uploaded_at"),
    )


# Re-export for migration discoverability
__all__ = [
    "GUID",
    "Account",
    "AccountHold",
    "AccountStatus",
    "ApiKey",
    "ApiKeyScope",
    "ApiKeyStatus",
    "AuditLog",
    "Base",
    "DataExport",
    "EmailInvite",
    "GatewayRequestLog",
    "OAuthAuthorizationCode",
    "OAuthClient",
    "OAuthIdentity",
    "ProcessedWebhook",
    "ProviderBalance",
    "ProviderCookies",
    "RequestPayload",
    "Seat",
    "SeatRole",
    "Session",
    "SubscriptionReminderSent",
    "Tariff",
    "TariffHistory",
    "Transaction",
    "TransactionKind",
    "UsageEvent",
    "User",
    "WelcomeCreditsLog",
]


# Register ORM event listeners (TariffHistory append-on-change, …).
# Done at import time so anywhere that touches models (FastAPI deps,
# Alembic env, tests) gets the listeners without a separate wiring
# step. ``register_listeners`` is idempotent.
from voltari_gateway.db.events import register_listeners as _register_listeners  # noqa: E402

_register_listeners()
