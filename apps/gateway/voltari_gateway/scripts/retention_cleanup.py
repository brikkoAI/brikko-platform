"""BrikkoLens — retention cleanup.

Phase 5 #4 Sprint 3 (2026-05-09).

Удаляет ``gateway_request_log`` row'ы старше ``BRIKKOLENS_RETENTION_DAYS``
дней (default = 56 = 8 недель).  Запускается раз в день systemd timer'ом
(см. ``infra/scripts/brikko-retention.{service,timer}``).

Logic
-----

* Рассчитываем cutoff = now() - retention_days
* Удаляем по chunks из 5000 row'ов, чтобы не блокировать read-traffic
* Логируем сколько удалено и за сколько секунд
* Безопасно к одновременному запуску: используем UPDATE/DELETE с
  per-row id, без транзакции на всю операцию

Future
------
* M6: вместо DELETE — переносить в "cold archive" S3 для Pro+ tier
  (10-летняя retention для compliance клиентов).  Пока MVP: DELETE.

Usage
-----

    python -m voltari_gateway.scripts.retention_cleanup

Поддерживает env vars:
* ``BRIKKOLENS_RETENTION_DAYS`` — окно retention (default 56)
* ``BRIKKOLENS_RETENTION_CHUNK`` — размер DELETE-чанка (default 5000)
* ``BRIKKOLENS_RETENTION_DRY_RUN`` — true → не удаляем, только считаем
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from voltari_gateway.db.models import GatewayRequestLog
from voltari_gateway.db.session import get_session_factory
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


DEFAULT_RETENTION_DAYS = 56  # 8 weeks
DEFAULT_CHUNK_SIZE = 5000


async def cleanup_old_traces(
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    dry_run: bool = False,
) -> int:
    """Delete trace rows older than ``retention_days``.  Returns count deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    factory = get_session_factory()

    async with factory() as session:
        # Сколько рядов под удаление
        count_stmt = (
            select(func.count())
            .select_from(GatewayRequestLog)
            .where(GatewayRequestLog.created_at < cutoff)
        )
        total = int((await session.execute(count_stmt)).scalar_one() or 0)

    if total == 0:
        log.info(
            "retention_cleanup_nothing_to_do",
            retention_days=retention_days,
            cutoff=cutoff.isoformat(),
        )
        return 0

    if dry_run:
        log.info(
            "retention_cleanup_dry_run",
            retention_days=retention_days,
            cutoff=cutoff.isoformat(),
            would_delete=total,
        )
        return total

    deleted_total = 0
    started = time.monotonic()

    while True:
        async with factory() as session:
            # Удаляем по id из подзапроса (PG/SQLite-совместимый паттерн).
            ids_stmt = (
                select(GatewayRequestLog.id)
                .where(GatewayRequestLog.created_at < cutoff)
                .limit(chunk_size)
            )
            ids = [r[0] for r in (await session.execute(ids_stmt)).all()]
            if not ids:
                break

            del_stmt = delete(GatewayRequestLog).where(GatewayRequestLog.id.in_(ids))
            await session.execute(del_stmt)
            await session.commit()
            deleted_total += len(ids)

        # Yield чтобы не монополизировать event loop под нагрузкой
        await asyncio.sleep(0)

    duration = time.monotonic() - started
    log.info(
        "retention_cleanup_done",
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
        deleted=deleted_total,
        duration_seconds=round(duration, 2),
    )
    return deleted_total


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


async def main() -> int:
    retention_days = int(os.environ.get("BRIKKOLENS_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
    chunk_size = int(os.environ.get("BRIKKOLENS_RETENTION_CHUNK") or DEFAULT_CHUNK_SIZE)
    dry_run = _bool_env("BRIKKOLENS_RETENTION_DRY_RUN")

    deleted = await cleanup_old_traces(
        retention_days=retention_days,
        chunk_size=chunk_size,
        dry_run=dry_run,
    )
    return 0 if deleted >= 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main()))
