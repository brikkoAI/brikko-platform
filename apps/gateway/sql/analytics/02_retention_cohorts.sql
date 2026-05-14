-- =====================================================================
-- 02_retention_cohorts.sql
-- Weekly cohort retention table: D1 / D7 / D30.
--
-- "Active" = аккаунт сделал ≥1 успешный API-запрос (строка в usage_events)
-- в окне [signup_at + N days, signup_at + (N+1) days).
--
-- Альтернативное определение "active" = ≥1 ₽ потрачено
--   (transactions.type='charge' AND amount_kopecks < 0):
--   полезно для финансового retention (платное использование). Сейчас
--   кладём в комментарий ниже как готовый swap. Для product-retention в
--   B2B SaaS принято ходить по событиям использования, а не по платежам,
--   так что usage_events — основной вариант.
--
-- Параметры:
--   :start_week — '2026-04-27' (первый понедельник cohort'а, inclusive)
--   :end_week   — '2026-08-01' (верхняя граница, exclusive)
--
-- GAP-ы:
--   * D30 retention доступен только для cohort'ов которым уже исполнилось
--     ≥30 дней — для свежих неделей колонка будет NULL. WHERE-фильтр в
--     самом конце скрывает преждевременные значения.
--   * Мы не различаем "сделал запрос но получил 4xx ошибку" vs "успешный
--     запрос". В usage_events сейчас попадают только биллинговые события
--     (после успешного ответа провайдера), так что это OK.
-- =====================================================================

WITH cohorts AS (
    SELECT
        a.id                                                       AS account_id,
        DATE_TRUNC('week', a.created_at AT TIME ZONE 'UTC')::date  AS cohort_week,
        a.created_at                                                AS signup_at,
        a.tariff
    FROM accounts a
    WHERE a.created_at >= :start_week::timestamptz
      AND a.created_at <  :end_week::timestamptz
),
activity AS (
    -- Все дни активности по каждому аккаунту (1 строка = 1 день, в котором был ≥1 запрос).
    SELECT DISTINCT
        ue.account_id,
        DATE_TRUNC('day', ue.created_at AT TIME ZONE 'UTC')::date AS active_day
    FROM usage_events ue
),
cohort_with_activity AS (
    SELECT
        c.cohort_week,
        c.account_id,
        c.tariff,
        a.active_day,
        (a.active_day - DATE_TRUNC('day', c.signup_at AT TIME ZONE 'UTC')::date) AS day_n
    FROM cohorts c
    LEFT JOIN activity a ON a.account_id = c.account_id
)
SELECT
    cohort_week,
    COUNT(DISTINCT account_id)                                              AS cohort_size,

    -- D1: вернулся в окно [+1d, +2d)
    COUNT(DISTINCT CASE WHEN day_n = 1 THEN account_id END)                 AS d1_active,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN day_n = 1 THEN account_id END)
                / NULLIF(COUNT(DISTINCT account_id),0), 1)                  AS d1_retention_pct,

    -- D7: вернулся хотя бы раз в окне [+7d, +14d) (стандарт product-analytics: «прошёл неделю и снова с нами»)
    COUNT(DISTINCT CASE WHEN day_n BETWEEN 7 AND 13 THEN account_id END)    AS d7_active,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN day_n BETWEEN 7 AND 13 THEN account_id END)
                / NULLIF(COUNT(DISTINCT account_id),0), 1)                  AS d7_retention_pct,

    -- D30: вернулся в окне [+30d, +37d). Только для cohort'ов >37 дней.
    CASE WHEN cohort_week + INTERVAL '37 days' < CURRENT_DATE THEN
        ROUND(100.0 * COUNT(DISTINCT CASE WHEN day_n BETWEEN 30 AND 36 THEN account_id END)
                    / NULLIF(COUNT(DISTINCT account_id),0), 1)
    END                                                                     AS d30_retention_pct
FROM cohort_with_activity
GROUP BY cohort_week
ORDER BY cohort_week DESC;

-- =====================================================================
-- Альтернативное определение "active" = ≥1 ₽ списано в день
-- (раскомментируй если хочешь financial retention):
--
--    activity AS (
--        SELECT DISTINCT
--            t.account_id,
--            DATE_TRUNC('day', t.created_at AT TIME ZONE 'UTC')::date AS active_day
--        FROM transactions t
--        WHERE t.type = 'charge' AND t.amount_kopecks < 0
--    )
-- =====================================================================
