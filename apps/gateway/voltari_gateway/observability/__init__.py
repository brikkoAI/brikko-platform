"""BrikkoLens — observability module.

Phase 5 #4 (2026-05-09). Provides per-request trace logging into
``gateway_request_log`` table. Frontend ``/app/traces`` reads from
this table.

Public API:
    record_request_log — fire-and-forget per-request logger.

Storage rationale: see docs/superpowers/specs/2026-05-09-brikkolens-
observability.md. Postgres without partitioning until ~5M req/мес;
ClickHouse not needed (Langfuse confirmed).
"""

from voltari_gateway.observability.request_log import (
    record_request_log,
    should_store_bodies,
)

__all__ = ["record_request_log", "should_store_bodies"]
