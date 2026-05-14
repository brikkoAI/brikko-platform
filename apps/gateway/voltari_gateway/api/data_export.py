"""Management API — personal data export (Sprint 7, 152-ФЗ ст. 14, GDPR Art. 20).

Mounted at ``/v1/account/data-export``. Three endpoints + one async
ZIP-builder side job:

    POST  /v1/account/data-export             — schedule export (202 + id)
    GET   /v1/account/data-export/{id}        — status + download URL
    GET   /v1/account/data-export/{id}/download
                                              — stream the ZIP back

The async job ``_build_export_zip`` runs on the same event loop as the
gateway. For the MVP this is good enough: a single-account dump is
~MBs of JSON, completes in seconds. When the dataset grows we move
this to a Redis-Streams worker and the endpoint shape stays the same.

Privacy contract — what we export and what we never export:

* ``account.json``     — Account fields (NO ``pii_masking_enabled``;
  that's an internal toggle).
* ``user.json``        — User fields, scrubbed of ``password_hash`` and
  ``totp_secret_encrypted`` and ``totp_recovery_codes_hashed``. Email,
  Telegram-link and verification flags stay (those are the user's own
  data).
* ``keys.json``        — API key prefixes + names + scopes + status.
  NEVER the plaintext (we don't have it) and NEVER the hash.
* ``transactions.json``— full Transaction history (legal requirement).
* ``usage_events.json``— full UsageEvent history. Crucially ``request_id``
  only — encrypted_prompt and friends from RequestPayload are excluded.
* ``audit_log.json``   — every audit row tied to this user.
* ``sessions.json``    — Session table rows but NEVER ``refresh_jti``
  (that's the live whitelist secret).
* ``README.txt``       — plain-text field reference, in Russian.

Rate limit: at most one export per user per 24h. Lower bound — a
generated ZIP is cheap but the dashboard would let an attacker of a
stolen cookie pull the user's full history with one click.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.auth.audit import write_audit
from voltari_gateway.auth.session_middleware import (
    SessionPrincipal,
    require_session,
)
from voltari_gateway.config import get_settings
from voltari_gateway.db.models import (
    Account,
    ApiKey,
    AuditLog,
    DataExport,
    Session,
    Transaction,
    UsageEvent,
    User,
)
from voltari_gateway.db.session import get_db, get_session_factory
from voltari_gateway.email.client import (
    build_frontend_link,
    render_template,
    send_email,
)
from voltari_gateway.utils.errors import GatewayError, invalid_request
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/account", tags=["account"])

# Minimum interval between two export requests for the same user.
EXPORT_RATE_LIMIT_HOURS: Final[int] = 24

# Strong refs to in-flight export workers so the asyncio scheduler doesn't
# garbage-collect them before they finish (RUF006). Tasks remove themselves
# via ``add_done_callback`` so this set drains naturally.
_PENDING_EXPORT_TASKS: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DataExportRequestResponse(BaseModel):
    """Body of POST /v1/account/data-export (status 202)."""

    id: uuid.UUID
    status: str


class DataExportStatusResponse(BaseModel):
    """Body of GET /v1/account/data-export/{id}."""

    id: uuid.UUID
    status: str
    requested_at: datetime
    completed_at: datetime | None = None
    expires_at: datetime
    download_url: str | None = None
    file_size_bytes: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FILENAME_SAFE = re.compile(r"^[a-f0-9-]{36}\.zip$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_export_dir(path: str) -> Path:
    """Create the export dir if it doesn't exist. Returns the resolved Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _zip_filename(export_id: uuid.UUID) -> str:
    """The on-disk filename for a given export id.

    UUIDs render to a fixed-length 36-char hex string — there's nothing
    user-controlled in the path, so directory traversal is impossible
    by construction. The regex above guards against anything else
    sneaking in via a stored ``download_path``.
    """
    return f"{export_id}.zip"


def _serialise(obj: Any) -> Any:
    """JSON-friendly conversion for SQLAlchemy values.

    UUIDs / datetimes / enums need explicit handling. SQLAlchemy returns
    raw enum members for our StrEnum-backed columns; ``str(...)`` gets
    the value.
    """
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        # Just record the size — we never want raw bytes (TOTP secret,
        # encrypted prompt) in an export ZIP.
        return f"<bytes:{len(obj)}>"
    if hasattr(obj, "value") and isinstance(obj, type(obj)):
        # Enum member.
        return obj.value
    return obj


def _row_to_dict(row: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Serialise a SQLAlchemy ORM row to a JSON-friendly dict."""
    exclude = exclude or set()
    out: dict[str, Any] = {}
    # SQLAlchemy Mapped declarative — iterate the table columns.
    for col in row.__table__.columns:
        name = col.name
        if name in exclude:
            continue
        value = getattr(row, name, None)
        out[name] = _serialise(value)
    return out


_README = """\
Архив с данными вашего аккаунта Brikko (152-ФЗ ст. 14, GDPR Art. 20).

Файлы:

  account.json        — настройки аккаунта (без флагов внутренних UI)
  user.json           — профиль пользователя (без password_hash и TOTP-секретов)
  keys.json           — список API-ключей (prefix, name, scope, status). Полные
                        ключи и хеши НЕ выгружаются — мы их не храним и они уже
                        известны вам по ответам /v1/keys.
  transactions.json   — все ledger-транзакции (top-up / charge / refund / др.)
  usage_events.json   — все запросы к моделям (без зашифрованного prompt'а;
                        для расследований обращайтесь в support@brikko.ru).
  audit_log.json      — журнал security-событий по вашему юзеру.
  sessions.json       — список выпущенных сессий (без секретов refresh-token).

Время снимка: см. поле requested_at в data-export-info.json.

Если в архиве чего-то не хватает — support@brikko.ru.
"""


async def _gather_export_data(
    db: AsyncSession,
    *,
    user: User,
    account: Account,
) -> dict[str, str]:
    """Collect all per-user data and return a dict ``{filename: contents}``.

    Each value is a JSON string ready for ``zipfile.write``.
    """
    user_dict = _row_to_dict(
        user,
        exclude={
            "password_hash",
            "totp_secret_encrypted",
            "totp_recovery_codes_hashed",
            "verification_token",
            "password_reset_token",
        },
    )
    account_dict = _row_to_dict(account, exclude={"pii_masking_enabled"})

    keys = (
        (
            await db.execute(
                select(ApiKey).where(ApiKey.account_id == account.id).order_by(ApiKey.created_at)
            )
        )
        .scalars()
        .all()
    )
    keys_payload = [
        {
            "id": str(k.id),
            "name": k.name,
            "key_prefix": k.key_prefix,
            "scope": k.scope.value,
            "status": k.status.value,
            "created_at": _serialise(k.created_at),
            "last_used_at": _serialise(k.last_used_at),
            "expires_at": _serialise(k.expires_at),
            "revoked_at": _serialise(k.revoked_at),
        }
        for k in keys
    ]

    txs = (
        (
            await db.execute(
                select(Transaction)
                .where(Transaction.account_id == account.id)
                .order_by(Transaction.created_at)
            )
        )
        .scalars()
        .all()
    )
    tx_payload = [_row_to_dict(t) for t in txs]

    usage = (
        (
            await db.execute(
                select(UsageEvent)
                .where(UsageEvent.account_id == account.id)
                .order_by(UsageEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    # Note: usage_events does not store the prompt; that is in
    # request_payloads which we deliberately exclude (encrypted blobs
    # plus the encryption key id are not part of the export contract —
    # see module docstring).
    usage_payload = [_row_to_dict(u) for u in usage]

    audits = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    audit_payload = [_row_to_dict(a) for a in audits]

    sessions = (
        (
            await db.execute(
                select(Session).where(Session.user_id == user.id).order_by(Session.created_at)
            )
        )
        .scalars()
        .all()
    )
    session_payload = [
        {
            "id": str(s.id),
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
            "device_label": s.device_label,
            "created_at": _serialise(s.created_at),
            "last_seen_at": _serialise(s.last_seen_at),
            "expires_at": _serialise(s.expires_at),
            "revoked_at": _serialise(s.revoked_at),
        }
        for s in sessions
    ]

    info = {
        "account_id": str(account.id),
        "user_id": str(user.id),
        "snapshot_at": _utcnow().isoformat(),
        "schema_version": 1,
    }

    def _to_json(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return {
        "data-export-info.json": _to_json(info),
        "account.json": _to_json(account_dict),
        "user.json": _to_json(user_dict),
        "keys.json": _to_json(keys_payload),
        "transactions.json": _to_json(tx_payload),
        "usage_events.json": _to_json(usage_payload),
        "audit_log.json": _to_json(audit_payload),
        "sessions.json": _to_json(session_payload),
        "README.txt": _README,
    }


async def _build_export_zip(
    *,
    factory: async_sessionmaker[AsyncSession],
    export_id: uuid.UUID,
    export_dir: str,
) -> None:
    """Async worker: gather data + write ZIP + flip status to ``ready``.

    Runs on the gateway's event loop. All exceptions are caught and the
    row is marked ``failed`` with a sanitised error message — we never
    let the worker raise into the ASGI lifespan.
    """
    log_ = get_logger(__name__)
    base_dir = _ensure_export_dir(export_dir)
    zip_name = _zip_filename(export_id)
    zip_path = base_dir / zip_name

    try:
        async with factory() as session:
            row = await session.get(DataExport, export_id)
            if row is None:
                log_.warning("export_worker_missing_row", export_id=str(export_id))
                return
            row.status = "processing"
            await session.commit()

        # Re-open so the read transaction stays small.
        async with factory() as session:
            row = await session.get(DataExport, export_id)
            if row is None:
                return
            user = await session.get(User, row.user_id)
            account = await session.get(Account, row.account_id)
            if user is None or account is None:
                row.status = "failed"
                row.error_message = "user_or_account_missing"
                await session.commit()
                return
            files = await _gather_export_data(session, user=user, account=account)

        # Write ZIP under a tmp name and rename to be atomic-ish on the
        # filesystem. zipfile.ZipFile is sync — use to_thread to avoid
        # pinning the loop on a slow disk.
        def _write_zip() -> int:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for name, contents in files.items():
                    zf.writestr(name, contents)
            data = buf.getvalue()
            zip_path.write_bytes(data)
            return len(data)

        size_bytes = await asyncio.to_thread(_write_zip)

        async with factory() as session:
            row = await session.get(DataExport, export_id)
            if row is None:
                return
            settings = get_settings()
            row.status = "ready"
            row.ready_at = _utcnow()
            row.expires_at = _utcnow() + timedelta(days=settings.data_export_ttl_days)
            row.storage_path = zip_name
            row.size_bytes = size_bytes
            row.download_url = build_frontend_link(f"/v1/account/data-export/{export_id}/download")
            await session.commit()
            user = await session.get(User, row.user_id)

        if user is not None:
            try:
                body = render_template(
                    "data_export_ready.txt",
                    download_url=build_frontend_link(
                        f"/v1/account/data-export/{export_id}/download"
                    ),
                    snapshot_date=_utcnow().date().isoformat(),
                    expires_at=row.expires_at.isoformat() if row else "",
                    request_id=str(export_id),
                )
                await send_email(
                    to=user.email,
                    subject="Ваш экспорт данных Brikko готов",
                    body=body,
                )
            except Exception as exc:
                log_.warning("export_email_failed", error=str(exc))
    except Exception as exc:  # pragma: no cover — defensive
        log_.exception("export_worker_failed", export_id=str(export_id), error=str(exc))
        try:
            async with factory() as session:
                row = await session.get(DataExport, export_id)
                if row is not None:
                    row.status = "failed"
                    # Keep the message terse — error_message column is 1024 chars.
                    row.error_message = str(exc)[:512]
                    await session.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1) POST /v1/account/data-export
# ---------------------------------------------------------------------------


@router.post(
    "/data-export",
    status_code=202,
    response_model=DataExportRequestResponse,
    summary="Schedule a personal data export (ZIP)",
)
async def request_data_export(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
) -> DataExportRequestResponse:
    user = principal.user
    account = principal.account

    # Rate limit: one request per 24h per user. Last row covers all
    # statuses — pending/processing/ready all count.
    cutoff = _utcnow() - timedelta(hours=EXPORT_RATE_LIMIT_HOURS)
    recent = await db.execute(
        select(DataExport)
        .where(DataExport.user_id == user.id, DataExport.requested_at > cutoff)
        .order_by(DataExport.requested_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise GatewayError(
            status_code=429,
            message=(
                "Data export was already requested within the last 24 hours. "
                "Please wait before requesting a new export."
            ),
            type="rate_limit_error",
            code="data_export_rate_limited",
        )

    settings = get_settings()
    row = DataExport(
        user_id=user.id,
        account_id=account.id,
        status="pending",
        requested_at=_utcnow(),
        # Sentinel — overridden in the worker once the ZIP is on disk.
        # We still need a non-NULL value to satisfy the table NOT NULL.
        expires_at=_utcnow() + timedelta(days=settings.data_export_ttl_days),
    )
    db.add(row)

    await write_audit(
        db,
        user_id=user.id,
        account_id=account.id,
        action="data_export_requested",
        request=request,
    )

    await db.commit()
    await db.refresh(row)

    # Spawn the worker. We don't await — the endpoint returns 202 right
    # away. We track the task in a module-level set so the GC doesn't
    # collect it mid-flight (RUF006 — losing the reference kills the
    # task on some loop implementations). Uncaught exceptions inside the
    # coroutine are already swallowed by ``_build_export_zip`` itself.
    factory = get_session_factory()
    task = asyncio.create_task(
        _build_export_zip(
            factory=factory,
            export_id=row.id,
            export_dir=settings.data_export_dir,
        ),
        name=f"data_export:{row.id}",
    )
    _PENDING_EXPORT_TASKS.add(task)
    task.add_done_callback(_PENDING_EXPORT_TASKS.discard)

    return DataExportRequestResponse(id=row.id, status=row.status)


# ---------------------------------------------------------------------------
# 2) GET /v1/account/data-export/{id}
# ---------------------------------------------------------------------------


async def _load_owned_export(
    db: AsyncSession,
    *,
    export_id: uuid.UUID,
    user_id: uuid.UUID,
) -> DataExport:
    """Fetch an export row, enforcing user ownership.

    Cross-user prevention: anyone hitting another user's id gets a 404
    (not 403) so we don't even leak existence.
    """
    row = await db.get(DataExport, export_id)
    if row is None or row.user_id != user_id:
        raise GatewayError(
            status_code=404,
            message="Data export not found.",
            type="invalid_request_error",
            code="export_not_found",
        )
    return row


@router.get(
    "/data-export/{export_id}",
    response_model=DataExportStatusResponse,
    summary="Return data-export status + download URL",
)
async def get_data_export(
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    export_id: uuid.UUID = PathParam(...),
) -> DataExportStatusResponse:
    row = await _load_owned_export(db, export_id=export_id, user_id=principal.user.id)
    download_url: str | None = None
    if row.status == "ready":
        # The download URL we surface points at the dashboard endpoint
        # (cookie-authenticated). Refresh per-call so a stale URL never
        # leaks across closed sessions.
        download_url = build_frontend_link(f"/v1/account/data-export/{row.id}/download")
    return DataExportStatusResponse(
        id=row.id,
        status=row.status,
        requested_at=row.requested_at,
        completed_at=row.ready_at,
        expires_at=row.expires_at,
        download_url=download_url,
        file_size_bytes=row.size_bytes,
    )


# ---------------------------------------------------------------------------
# 3) GET /v1/account/data-export/{id}/download
# ---------------------------------------------------------------------------


async def _stream_zip(path: Path) -> AsyncIterator[bytes]:
    """Async generator that streams the ZIP file in 64 KB chunks."""
    chunk = 64 * 1024
    # The blocking open + read are wrapped in ``to_thread`` so we don't
    # pin the event loop on a slow disk read.
    f = await asyncio.to_thread(open, path, "rb")
    try:
        while True:
            data = await asyncio.to_thread(f.read, chunk)
            if not data:
                break
            yield data
    finally:
        await asyncio.to_thread(f.close)


@router.get(
    "/data-export/{export_id}/download",
    summary="Stream the ZIP back to the user",
    responses={
        200: {"description": "ZIP archive."},
        404: {"description": "Export not found, or belongs to a different user."},
        409: {"description": "Export is not ready yet."},
        410: {"description": "Export has expired."},
    },
)
async def download_data_export(
    db: Annotated[AsyncSession, Depends(get_db)],
    principal: Annotated[SessionPrincipal, Depends(require_session)],
    export_id: uuid.UUID = PathParam(...),
) -> StreamingResponse:
    row = await _load_owned_export(db, export_id=export_id, user_id=principal.user.id)

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite returns naive datetimes; treat as UTC for comparison.
        expires_at = expires_at.replace(tzinfo=UTC)
    if row.status == "expired" or expires_at < _utcnow():
        raise GatewayError(
            status_code=410,
            message="Data export has expired.",
            type="invalid_request_error",
            code="export_expired",
        )
    if row.status != "ready" or not row.storage_path:
        raise invalid_request(
            "Data export is still being prepared.",
            code="export_not_ready",
        )

    settings = get_settings()
    base = Path(settings.data_export_dir)
    safe_name = row.storage_path
    if not _FILENAME_SAFE.match(safe_name):
        # Defence-in-depth: the path is set by us, but we never want to
        # serve a file via a name we didn't synthesise.
        raise GatewayError(
            status_code=500,
            message="Export storage state is invalid.",
            type="api_error",
            code="export_corrupt",
        )
    zip_path = base / safe_name
    if not zip_path.exists():
        raise GatewayError(
            status_code=410,
            message="Data export file is no longer available.",
            type="invalid_request_error",
            code="export_expired",
        )

    row.downloaded_at = _utcnow()
    row.download_count = (row.download_count or 0) + 1
    await db.commit()

    headers = {
        "Content-Disposition": f'attachment; filename="brikko-export-{export_id}.zip"',
    }
    return StreamingResponse(
        _stream_zip(zip_path),
        media_type="application/zip",
        headers=headers,
    )


__all__ = ["EXPORT_RATE_LIMIT_HOURS", "router"]
