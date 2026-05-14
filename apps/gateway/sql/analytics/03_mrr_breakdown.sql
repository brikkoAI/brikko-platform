-- =====================================================================
-- 03_mrr_breakdown.sql
-- MRR breakdown за текущий месяц + дельты (new / churn / expansion).
--
-- Параметры:
--   :month_start  — '2026-05-01' (первый день текущего месяца UTC)
--   :prev_start   — '2026-04-01' (первый день предыдущего месяца)
--
-- Контракт «MRR = ₽ списанных с balance + ₽ subscription-charge'ов
--               за календарный месяц по тарифу аккаунта на момент списания»
-- Это hybrid: PAYG-баланс + подписочные SKU (Pro/Pro Privacy/Team/Business),
-- которые пишутся как transactions.type='subscription' (kind enum в models.py).
-- Это упрощение vs MRR-по-определению-bookings, но:
--   * у нас на старте нет долгих контрактов (всё month-to-month),
--   * единственный legal-факт — это что списано из balance — поэтому это
--     самая «правдивая» цифра для CEO в ранние месяцы.
--
-- Sprint 11 (Alembic 0013) — добавлена tariff_history. Теперь точно
-- знаем какой тариф был активен в момент списания: для каждой
-- транзакции делаем lateral lookup последней записи tariff_history
-- с changed_at <= transactions.created_at. Это позволяет:
--   * Корректно атрибутировать revenue к тарифу даже если
--     accounts.tariff поменялся середине месяца.
--   * Считать expansion MRR как сумму revenue, у которого tariff_at_charge
--     отличается от tariff_at_prev_month — в Части Б ниже.
-- =====================================================================

-- =====================================================================
-- Часть А. MRR by tariff за текущий месяц (point-in-time tariff)
-- =====================================================================
WITH tx_with_tariff AS (
    SELECT
        t.id                                                          AS tx_id,
        t.account_id,
        t.amount_kopecks,
        t.created_at,
        -- LATERAL — для Postgres; берём последнюю tariff_history запись
        -- с changed_at <= t.created_at. Если ни одной записи нет
        -- (legacy account до Alembic 0013) — fallback на a.tariff.
        COALESCE(th.to_tariff, a.tariff)                              AS tariff_at_charge
    FROM transactions t
    JOIN accounts a ON a.id = t.account_id
    LEFT JOIN LATERAL (
        SELECT to_tariff
        FROM tariff_history h
        WHERE h.account_id = t.account_id
          AND h.changed_at <= t.created_at
        ORDER BY h.changed_at DESC
        LIMIT 1
    ) th ON TRUE
    WHERE t.created_at >= :month_start::timestamptz
      AND t.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
      AND t.type IN ('charge', 'subscription', 'autorefill')
),
revenue_current AS (
    SELECT
        tariff_at_charge AS tariff,
        account_id,
        SUM(ABS(amount_kopecks)) AS revenue_kopecks
    FROM tx_with_tariff
    GROUP BY tariff_at_charge, account_id
)
SELECT
    tariff,
    COUNT(DISTINCT account_id)                                  AS paying_accounts,
    SUM(revenue_kopecks) / 100.0                                AS mrr_rub,
    ROUND(AVG(revenue_kopecks) / 100.0, 0)                      AS arpu_rub,
    -- ARPPU == ARPU здесь по построению (отбираем только платящих).
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY revenue_kopecks) / 100.0, 0) AS median_revenue_rub,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY revenue_kopecks) / 100.0, 0) AS p90_revenue_rub
FROM revenue_current
GROUP BY tariff
ORDER BY mrr_rub DESC NULLS LAST;


-- =====================================================================
-- Часть Б. MRR delta: new / churn / expansion / contraction
-- (с учётом tariff_history — apgrade посреди месяца теперь виден)
-- =====================================================================
-- Запускать отдельно (psql выполняет statement за statement).
WITH revenue_curr AS (
    SELECT
        t.account_id,
        SUM(ABS(t.amount_kopecks)) AS rev_kop
    FROM transactions t
    WHERE t.created_at >= :month_start::timestamptz
      AND t.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
      AND t.type IN ('charge', 'subscription', 'autorefill')
    GROUP BY t.account_id
),
revenue_prev AS (
    SELECT
        t.account_id,
        SUM(ABS(t.amount_kopecks)) AS rev_kop
    FROM transactions t
    WHERE t.created_at >= :prev_start::timestamptz
      AND t.created_at <  (:prev_start::timestamptz + INTERVAL '1 month')
      AND t.type IN ('charge', 'subscription', 'autorefill')
    GROUP BY t.account_id
),
joined AS (
    SELECT
        COALESCE(c.account_id, p.account_id) AS account_id,
        COALESCE(p.rev_kop, 0)               AS prev_kop,
        COALESCE(c.rev_kop, 0)               AS curr_kop
    FROM revenue_curr c
    FULL OUTER JOIN revenue_prev p ON p.account_id = c.account_id
),
-- Кольцо тарифов в начале текущего месяца — используем для разделения
-- expansion (апгрейд тарифа) от просто-больше-потратил-внутри-PAYG.
tariff_at_curr_start AS (
    SELECT DISTINCT ON (account_id)
        account_id,
        to_tariff AS tariff
    FROM tariff_history
    WHERE changed_at <= :month_start::timestamptz
    ORDER BY account_id, changed_at DESC
),
tariff_at_prev_start AS (
    SELECT DISTINCT ON (account_id)
        account_id,
        to_tariff AS tariff
    FROM tariff_history
    WHERE changed_at <= :prev_start::timestamptz
    ORDER BY account_id, changed_at DESC
)
SELECT
    -- New MRR: впервые появились в curr
    SUM(CASE WHEN j.prev_kop = 0 AND j.curr_kop > 0 THEN j.curr_kop ELSE 0 END) / 100.0 AS new_mrr_rub,

    -- Churn MRR: были в prev, выпали в curr
    SUM(CASE WHEN j.prev_kop > 0 AND j.curr_kop = 0 THEN -j.prev_kop ELSE 0 END) / 100.0 AS churn_mrr_rub,

    -- Expansion MRR: оба есть, curr > prev (апгрейд / больше тратит)
    SUM(CASE WHEN j.prev_kop > 0 AND j.curr_kop > j.prev_kop
             THEN j.curr_kop - j.prev_kop ELSE 0 END) / 100.0 AS expansion_mrr_rub,

    -- Contraction MRR: оба есть, curr < prev (даунгрейд / меньше тратит)
    SUM(CASE WHEN j.prev_kop > 0 AND j.curr_kop > 0 AND j.curr_kop < j.prev_kop
             THEN -(j.prev_kop - j.curr_kop) ELSE 0 END) / 100.0 AS contraction_mrr_rub,

    -- Net MRR change
    SUM(j.curr_kop - j.prev_kop) / 100.0 AS net_mrr_change_rub,

    -- Прошлый и текущий total для сверки
    SUM(j.prev_kop) / 100.0 AS prev_month_total_rub,
    SUM(j.curr_kop) / 100.0 AS curr_month_total_rub,

    -- Дополнительно: сколько аккаунтов реально апгрейднулись к более
    -- дорогому тарифу (отличный сигнал от "больше потратил на PAYG").
    -- Не входит в формулу expansion_mrr_rub — только метаданные.
    COUNT(DISTINCT CASE
        WHEN tc.tariff IS NOT NULL AND tp.tariff IS NOT NULL
             AND tc.tariff <> tp.tariff
        THEN j.account_id
    END) AS tariff_changed_accounts
FROM joined j
LEFT JOIN tariff_at_curr_start tc ON tc.account_id = j.account_id
LEFT JOIN tariff_at_prev_start tp ON tp.account_id = j.account_id;


-- =====================================================================
-- Часть В. ARPU/ARPPU по всем активным (для сверки, опционально)
-- =====================================================================
SELECT
    COUNT(DISTINCT a.id)                                            AS total_active_accounts,
    COUNT(DISTINCT rc.account_id)                                   AS paying_accounts,
    ROUND(100.0 * COUNT(DISTINCT rc.account_id)
                 / NULLIF(COUNT(DISTINCT a.id), 0), 1)              AS payer_share_pct,
    SUM(COALESCE(rc.rev_kop, 0)) / 100.0                            AS total_revenue_rub,
    -- ARPU: на всех active
    ROUND(SUM(COALESCE(rc.rev_kop, 0)) / 100.0
        / NULLIF(COUNT(DISTINCT a.id), 0), 0)                       AS arpu_rub,
    -- ARPPU: только платящие
    ROUND(SUM(COALESCE(rc.rev_kop, 0)) / 100.0
        / NULLIF(COUNT(DISTINCT rc.account_id), 0), 0)              AS arppu_rub
FROM accounts a
LEFT JOIN (
    SELECT t.account_id, SUM(ABS(t.amount_kopecks)) AS rev_kop
    FROM transactions t
    WHERE t.created_at >= :month_start::timestamptz
      AND t.created_at <  (:month_start::timestamptz + INTERVAL '1 month')
      AND t.type IN ('charge', 'subscription', 'autorefill')
    GROUP BY t.account_id
) rc ON rc.account_id = a.id
WHERE a.status = 'active';
