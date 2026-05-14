"""Background sweeper that expires + cleans up data export ZIPs.

Lifecycle of a ``DataExport`` row:

    pending → processing → ready → (expired | purged)

This module owns the ``ready → expired`` transition (the row's
``expires_at`` boundary) and removes the on-disk ZIP. The fresh-export
async job (``api/data_export.py::_build_export_zip``) owns
``pending → processing → ready``.

Trade-offs
----------

* Disk-only storage (V1). When we move to S3 the cleanup logic moves
  upstream; the DB row already has ``storage_path`` so the cron just
  swaps a ``Path.unlink`` for an ``S3.delete_object``.
* We mark the row ``expired`` in the same SQL update that sets
  ``download_url=NULL`` so a stale dashboard link returns 410 the
  moment the cron lands.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voltari_gateway.db.models import DataExport
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


def _resolve_zip_path(export_dir: str, row: DataExport) -> Path | None:
    """Resolve the on-disk ZIP path for a row, if any.

    ``storage_path`` is stored relative to the configured export dir so
    the same DB row stays valid across deployments that mount the
    storage volume at different absolute paths.
    """
    if not row.storage_path:
        return None
    base = Path(export_dir)
    p = base / row.storage_path
    return p


async def sweep_expired_exports(
    db: AsyncSession,
    *,
    export_dir: str,
) -> int:
    """Expire ready exports past their TTL and unlink their ZIPs.

    Returns the count of rows transitioned to ``expired`` in this sweep.
    """
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(DataExport).where(
                    DataExport.status == "ready",
                    DataExport.expires_at < now,
                )
            )
        )
        .scalars()
        .all()
    )

    swept = 0
    for row in rows:
        zip_path = _resolve_zip_path(export_dir, row)
        if zip_path is not None:
            try:
                if zip_path.exists():
                    os.unlink(zip_path)
            except OSError as exc:
                # Logging is enough — the DB row will still flip to
                # ``expired`` and the file will leak. Ops can find these
                # via the storage_path index.
                log.warning(
                    "data_export_unlink_failed",
                    export_id=str(row.id),
                    path=str(zip_path),
                    error=str(exc),
                )
        row.status = "expired"
        row.download_url = None
        swept += 1

    return swept


async def data_export_cleanup_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    export_dir: str,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    log.info(
        "data_export_cleanup_loop_started",
        interval_seconds=interval_seconds,
        export_dir=export_dir,
    )
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                swept = await sweep_expired_exports(session, export_dir=export_dir)
                await session.commit()
            if swept:
                log.info("data_export_cleanup_tick", exports_expired=swept)
        except Exception as exc:
            log.warning("data_export_cleanup_tick_failed", error=str(exc))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    log.info("data_export_cleanup_loop_stopped")


__all__ = [
    "data_export_cleanup_loop",
    "sweep_expired_exports",
]
