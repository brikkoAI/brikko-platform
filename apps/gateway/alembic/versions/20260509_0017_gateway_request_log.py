"""BrikkoLens Sprint 1 — gateway_request_log таблица для observability.

Revision ID: 0017_gateway_request_log
Revises: 0016_oauth_identities
Create Date: 2026-05-09

Phase 5 #4 BrikkoLens MVP — встроенная observability для клиентов
Pro/Team. Каждый запрос к /v1/chat/completions (и аналогам) пишется
сюда. Frontend `/app/traces` строится поверх этой таблицы.

Архитектурные решения (см. 06_Operations/2026-05-09-night-research/
14-brikkolens-observability.md):

* Postgres (без ClickHouse — не нужен до 5M req/мес).
* Cost attribution at ingest time (вычисляем cost при записи, не в SQL).
* OTel-совместимость через view `v_otel_spans`.

Partitioning отложено до M6: research предлагал RANGE-партиции по
неделям, но конвертация добавит сложности и сейчас при 0 активных
клиентах overhead не оправдан. Когда объём вырастет — добавим миграцию
которая создаст partitioned-двойник + COPY data + DETACH старую.

Параллельная таблица `usage_events` остаётся для billing-aggregation —
в ней нет полей status/latency/error/streaming-flags. Эти таблицы
пересекаются по {account_id, api_key_id, model, provider, request_id,
input_tokens, output_tokens, cost_kopecks} но дают разный shape для
разных потребителей (биллинг vs observability).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0017_gateway_request_log"
down_revision = "0016_oauth_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE gateway_request_log (
          id            uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
          account_id    uuid          NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
          api_key_id    uuid          NULL REFERENCES api_keys(id) ON DELETE SET NULL,
          request_id    text          NOT NULL,
          trace_id      uuid          NULL,
          parent_id     uuid          NULL,

          -- routing
          provider      text          NOT NULL,
          model         text          NOT NULL,
          routed_from   text          NULL,

          -- timings
          started_at    timestamptz   NOT NULL,
          finished_at   timestamptz   NOT NULL,
          latency_ms    integer       NOT NULL,
          ttft_ms       integer       NULL,

          -- usage
          prompt_tokens     integer   NOT NULL DEFAULT 0,
          completion_tokens integer   NOT NULL DEFAULT 0,
          cached_tokens     integer   NOT NULL DEFAULT 0,
          reasoning_tokens  integer   NOT NULL DEFAULT 0,
          total_tokens      integer   GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,

          -- cost (computed at ingest from price catalog)
          cost_kop      bigint        NOT NULL DEFAULT 0,
          fx_usd_rub    numeric(8,4)  NULL,

          -- status / error
          status        text          NOT NULL,
          http_code     integer       NULL,
          error_code    text          NULL,
          error_message text          NULL,

          -- flags
          is_streaming  boolean       NOT NULL DEFAULT false,
          cache_hit     boolean       NOT NULL DEFAULT false,
          pii_masked    boolean       NOT NULL DEFAULT false,
          tools_used    boolean       NOT NULL DEFAULT false,

          -- bodies (opt-in via account.store_prompts)
          request_body  jsonb         NULL,
          response_body jsonb         NULL,

          -- model params + custom user metadata
          model_params  jsonb         NULL,
          metadata      jsonb         NULL,

          created_at    timestamptz   NOT NULL DEFAULT now()
        );
        """
    )

    # Indexes — поддержка типичных запросов /app/traces UI.
    # 1. Поиск конкретного request_id внутри account.
    op.execute(
        """
        CREATE INDEX idx_gateway_request_log_request_id
          ON gateway_request_log (account_id, request_id);
        """
    )
    # 2. Аналитика по api_key + recency (table-scan по последним N).
    op.execute(
        """
        CREATE INDEX idx_gateway_request_log_api_key_recent
          ON gateway_request_log (api_key_id, created_at DESC);
        """
    )
    # 3. Аналитика per-model.
    op.execute(
        """
        CREATE INDEX idx_gateway_request_log_model_recent
          ON gateway_request_log (account_id, model, created_at DESC);
        """
    )
    # 4. Errors-only — partial index для быстрого фильтра /app/traces?status=error.
    op.execute(
        """
        CREATE INDEX idx_gateway_request_log_errors
          ON gateway_request_log (account_id, created_at DESC)
          WHERE status != 'ok';
        """
    )
    # 5. Generic recency index для главного списка.
    op.execute(
        """
        CREATE INDEX idx_gateway_request_log_account_recent
          ON gateway_request_log (account_id, created_at DESC);
        """
    )

    # OTel-совместимый view (read-only) — для интеграции с любым OTel-backend
    # без переписывания нашей трассировки.
    op.execute(
        """
        CREATE VIEW v_otel_spans AS
        SELECT
          id::text                                 AS span_id,
          COALESCE(trace_id, id)::text             AS trace_id,
          parent_id::text                          AS parent_span_id,
          'chat'                                   AS gen_ai_operation_name,
          provider                                 AS gen_ai_provider_name,
          model                                    AS gen_ai_request_model,
          prompt_tokens                            AS gen_ai_usage_input_tokens,
          completion_tokens                        AS gen_ai_usage_output_tokens,
          cached_tokens                            AS gen_ai_usage_cache_token_usage,
          reasoning_tokens                         AS gen_ai_usage_reasoning_output_tokens,
          latency_ms                               AS duration_ms,
          ttft_ms                                  AS gen_ai_server_time_to_first_token,
          error_code                               AS error_type,
          is_streaming                             AS gen_ai_request_stream,
          status,
          started_at,
          finished_at,
          created_at,
          account_id,
          api_key_id
        FROM gateway_request_log;
        """
    )

    # Daily aggregation для analytics dashboard.
    # Materialized view → REFRESH CONCURRENTLY каждые 5 мин из cron.
    # Без него UI с percentile_cont на каждом запросе тормозит на >100k rows.
    op.execute(
        """
        CREATE MATERIALIZED VIEW gateway_request_daily AS
        SELECT
          account_id,
          api_key_id,
          model,
          provider,
          date_trunc('day', created_at)              AS day,
          count(*)                                   AS requests,
          count(*) FILTER (WHERE status = 'error')   AS errors,
          count(*) FILTER (WHERE cache_hit)          AS cache_hits,
          count(*) FILTER (WHERE tools_used)         AS tool_calls,
          sum(prompt_tokens)                         AS prompt_tokens,
          sum(completion_tokens)                     AS completion_tokens,
          sum(cached_tokens)                         AS cached_tokens,
          sum(cost_kop)                              AS cost_kop,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)  AS latency_p50_ms,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS latency_p95_ms
        FROM gateway_request_log
        GROUP BY 1, 2, 3, 4, 5
        WITH NO DATA;
        """
    )
    # Обязательный для CONCURRENTLY refresh.
    op.execute(
        """
        CREATE UNIQUE INDEX idx_gateway_request_daily_uq
          ON gateway_request_daily (account_id, api_key_id, model, provider, day);
        """
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS gateway_request_daily;")
    op.execute("DROP VIEW IF EXISTS v_otel_spans;")
    op.execute("DROP TABLE IF EXISTS gateway_request_log;")
