-- =====================================================================
-- 04_provider_mix.sql
-- Last 30 days: распределение трафика и выручки по 6 провайдерам.
--
-- Sprint 11 (Alembic 0013): теперь используем настоящую колонку
--   usage_events.provider — она populate'ится в gateway при логировании
--   события (chosen.provider.value). Backfill старых строк сделан
--   миграцией через CASE-маппинг по prefix model_id. Индекс
--   ix_usage_events_provider_created (provider, created_at) делает
--   30-дневное окно дешёвым (~ms на 100k событий).
--
-- Параметры:
--   :since — '2026-04-01' (начало 30-дневного окна; обычно NOW() - 30d)
-- =====================================================================

WITH events AS (
    SELECT
        ue.account_id,
        ue.input_tokens,
        ue.output_tokens,
        ue.cached_tokens,
        ue.cost_kopecks,
        ue.provider
    FROM usage_events ue
    WHERE ue.created_at >= :since::timestamptz
      AND ue.created_at <  :since::timestamptz + INTERVAL '30 days'
)
SELECT
    provider,
    COUNT(*)                                                                AS request_count,
    COUNT(DISTINCT account_id)                                              AS unique_accounts,
    SUM(input_tokens)                                                       AS input_tokens_total,
    SUM(output_tokens)                                                      AS output_tokens_total,
    SUM(input_tokens + output_tokens)                                       AS total_tokens,
    SUM(cached_tokens)                                                      AS cached_tokens_total,
    ROUND(100.0 * SUM(cached_tokens) / NULLIF(SUM(input_tokens),0), 1)      AS cache_hit_pct,

    -- Доли от общего объёма
    ROUND(100.0 * SUM(input_tokens + output_tokens) /
          SUM(SUM(input_tokens + output_tokens)) OVER (), 1)                AS share_of_tokens_pct,
    ROUND(100.0 * COUNT(*) /
          SUM(COUNT(*)) OVER (), 1)                                         AS share_of_requests_pct,
    ROUND(100.0 * SUM(cost_kopecks) /
          NULLIF(SUM(SUM(cost_kopecks)) OVER (), 0), 1)                     AS share_of_revenue_pct,

    -- COGS и выручка
    SUM(cost_kopecks) / 100.0                                               AS revenue_rub,
    -- ARPC (Avg Revenue Per Call)
    ROUND(AVG(cost_kopecks) / 100.0, 2)                                     AS avg_revenue_per_call_rub
FROM events
GROUP BY provider
ORDER BY total_tokens DESC NULLS LAST;

-- =====================================================================
-- Доп. срез — top-3 модели внутри каждого провайдера (для понимания
-- какая именно модель доминирует и где можно подкрутить ставку).
-- =====================================================================
SELECT
    ue.provider,
    ue.model,
    SUM(ue.input_tokens + ue.output_tokens)         AS total_tokens,
    SUM(ue.cost_kopecks) / 100.0                    AS revenue_rub,
    COUNT(*)                                        AS request_count
FROM usage_events ue
WHERE ue.created_at >= :since::timestamptz
  AND ue.created_at <  :since::timestamptz + INTERVAL '30 days'
GROUP BY ue.provider, ue.model
ORDER BY ue.provider, total_tokens DESC;
