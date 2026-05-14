-- =====================================================================
-- 05_top_users.sql
-- Топ-20 аккаунтов по тратам за месяц + power-user lens.
--
-- Параметры:
--   :month_start — '2026-05-01'
--
-- Что показываем:
--   1. Топ-20 по тратам ₽ за месяц.
--   2. Доля выручки от топ-10% аккаунтов (power-user concentration).
--   3. Доля выручки от топ-1 (single-customer risk — критично для B2B
--      на ранней стадии: если одна Business-компания делает 80% MRR,
--      её churn = catastrophe).
--
-- GAP-ы:
--   * Нет колонки accounts.company_name отдельно от accounts.name —
--     для Team/Business сейчас name = название юрлица, для PAYG/Pro =
--     "Personal" по дефолту. ОК на старте, но в будущем добавить
--     отдельную customer_name (для legal entity vs display name).
-- =====================================================================

-- ----------------------------------------------------------------
-- Часть А. Топ-20 по тратам за месяц
-- ----------------------------------------------------------------
WITH monthly_spend AS (
    SELECT
        a.id                                          AS account_id,
        a.name                                        AS account_name,
        a.tariff,
        u.email,
        SUM(ABS(t.amount_kopecks))                    AS spend_kopecks,
        COUNT(*) FILTER (WHERE t.type = 'charge')     AS charge_count,
        SUM(CASE WHEN t.type = 'topup' THEN t.amount_kopecks ELSE 0 END) AS topup_kopecks,
        a.balance_kopecks                             AS current_balance_kopecks
    FROM accounts a
    JOIN users u ON u.id = a.owner_id
    LEFT JOIN transactions t ON t.account_id = a.id
        AND t.created_at >= :month_start::timestamptz
        AND t.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
    GROUP BY a.id, a.name, a.tariff, u.email, a.balance_kopecks
    HAVING SUM(ABS(t.amount_kopecks)) > 0
)
SELECT
    ROW_NUMBER() OVER (ORDER BY spend_kopecks DESC)              AS rank,
    account_id,
    email,
    account_name,
    tariff,
    spend_kopecks / 100.0                                        AS spend_rub,
    charge_count                                                 AS api_calls_charged,
    topup_kopecks / 100.0                                        AS topups_this_month_rub,
    current_balance_kopecks / 100.0                              AS current_balance_rub
FROM monthly_spend
ORDER BY spend_kopecks DESC
LIMIT 20;


-- ----------------------------------------------------------------
-- Часть Б. Power-user concentration (Pareto check)
-- ----------------------------------------------------------------
WITH spend AS (
    SELECT
        t.account_id,
        SUM(ABS(t.amount_kopecks)) AS spend_kop
    FROM transactions t
    WHERE t.created_at >= :month_start::timestamptz
      AND t.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
      AND t.type IN ('charge', 'subscription', 'autorefill')
    GROUP BY t.account_id
),
ranked AS (
    SELECT
        account_id,
        spend_kop,
        NTILE(10) OVER (ORDER BY spend_kop DESC)        AS decile,
        ROW_NUMBER() OVER (ORDER BY spend_kop DESC)     AS rn,
        SUM(spend_kop) OVER ()                          AS total_kop,
        COUNT(*) OVER ()                                AS total_accounts
    FROM spend
)
SELECT
    'top_1'         AS segment,
    1               AS account_count,
    MAX(CASE WHEN rn = 1 THEN spend_kop END) / 100.0       AS spend_rub,
    ROUND(100.0 * MAX(CASE WHEN rn = 1 THEN spend_kop END) / NULLIF(MAX(total_kop), 0), 1) AS share_of_revenue_pct
FROM ranked
UNION ALL
SELECT
    'top_10pct',
    COUNT(*) FILTER (WHERE decile = 1),
    SUM(spend_kop) FILTER (WHERE decile = 1) / 100.0,
    ROUND(100.0 * SUM(spend_kop) FILTER (WHERE decile = 1) / NULLIF(MAX(total_kop), 0), 1)
FROM ranked
UNION ALL
SELECT
    'top_25pct',
    COUNT(*) FILTER (WHERE decile <= 3),  -- ≈30%, более точное «top-25%» требует NTILE(4)
    SUM(spend_kop) FILTER (WHERE decile <= 3) / 100.0,
    ROUND(100.0 * SUM(spend_kop) FILTER (WHERE decile <= 3) / NULLIF(MAX(total_kop), 0), 1)
FROM ranked
UNION ALL
SELECT
    'all_paying',
    MAX(total_accounts),
    MAX(total_kop) / 100.0,
    100.0
FROM ranked;


-- ----------------------------------------------------------------
-- Часть В. Token-power-user (по объёму, не по ₽)
-- — для понимания "кто потребляет токенами но мало платит"
--   (cheap-tier abuse / heavy use of self-hosted models)
-- ----------------------------------------------------------------
SELECT
    a.id                                  AS account_id,
    u.email,
    a.tariff,
    SUM(ue.input_tokens + ue.output_tokens) AS total_tokens,
    SUM(ue.cost_kopecks) / 100.0           AS revenue_rub,
    -- Heavy-but-cheap detector:
    --   высокий tokens / низкий cost → клиент гонит трафик через
    --   дешёвые модели (deepseek/gigachat-lite), не приносит выручки.
    ROUND(SUM(ue.cost_kopecks) * 100.0
        / NULLIF(SUM(ue.input_tokens + ue.output_tokens), 0), 4)
        AS kop_per_1k_tokens
FROM usage_events ue
JOIN accounts a ON a.id = ue.account_id
JOIN users u    ON u.id = a.owner_id
WHERE ue.created_at >= :month_start::timestamptz
  AND ue.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
GROUP BY a.id, u.email, a.tariff
ORDER BY total_tokens DESC
LIMIT 20;
