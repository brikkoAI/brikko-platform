-- =====================================================================
-- 01_activation_funnel.sql
-- Activation funnel за период [:start_date, :end_date) (по UTC).
-- Считает 6 шагов и conv-rate каждого относительно регистраций.
--
-- Параметры (psql variables):
--   :start_date  — '2026-05-01' (cohort lower bound, inclusive)
--   :end_date    — '2026-06-01' (upper bound, exclusive)
--
-- Использование:
--   psql ... -v start_date="'2026-05-01'" -v end_date="'2026-06-01'" \
--            -f 01_activation_funnel.sql
--
-- ВАЖНО про Activation:
--   * PAYG-аккаунт считаем активированным после "первого API-запроса"
--     (он-же ≥1 строка в usage_events), потому что pop-up 200 ₽ welcome
--     credits снимает требование на пополнение.
--   * Pro/Pro Privacy/Team/Business — после "первого пополнения"
--     (transactions.type='topup' успешный, т.е. строка вообще есть в
--     transactions таблице — webhook прошёл фильтр идемпотентности).
--   Вычисляем оба варианта в одном запросе, ниже выбираем relevant.
--
-- GAP-ы (на момент 2026-05-01):
--   * Нет колонки accounts.activated_at — рассчитываем на лету через
--     min(usage_events.created_at). Это OK на ≤10 тыс. accounts; при росте
--     добавить материализованную колонку и заполнять триггером
--     (alembic migration: ALTER TABLE accounts ADD activated_at TIMESTAMPTZ).
--   * Нет колонки accounts.acquisition_channel — невозможно фильтровать
--     funnel по каналу. Добавить utm_source / referrer на /v1/auth/signup.
-- =====================================================================

WITH cohort AS (
    -- Все аккаунты, созданные в окне (один аккаунт = один user через owner_id).
    SELECT
        a.id              AS account_id,
        a.owner_id        AS user_id,
        a.tariff,
        a.created_at      AS account_created_at,
        u.email_verified,
        u.created_at      AS user_created_at
    FROM accounts a
    JOIN users u ON u.id = a.owner_id
    WHERE a.created_at >= :start_date::timestamptz
      AND a.created_at <  :end_date::timestamptz
),
first_key AS (
    SELECT account_id, MIN(created_at) AS first_key_at
    FROM api_keys
    GROUP BY account_id
),
first_topup AS (
    SELECT account_id, MIN(created_at) AS first_topup_at
    FROM transactions
    WHERE type = 'topup'           -- успешные топ-апы (welcome bonus сюда не попадает: он залит как 'topup' с ref_id=welcome:..., см. billing.engine credit_account)
      AND amount_kopecks > 0
    GROUP BY account_id
),
first_request AS (
    SELECT account_id, MIN(created_at) AS first_request_at
    FROM usage_events
    GROUP BY account_id
),
fifth_request AS (
    -- 5-й по счёту запрос — proxy-метрика habit-loop (≥5 = нащупал value).
    SELECT account_id, created_at AS fifth_request_at
    FROM (
        SELECT
            account_id,
            created_at,
            ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at) AS rn
        FROM usage_events
    ) t
    WHERE rn = 5
)
SELECT
    -- ---------- абсолютные числа ----------
    COUNT(*)                                                  AS step_0_signed_up,
    COUNT(*) FILTER (WHERE c.email_verified)                  AS step_1_email_verified,
    COUNT(fk.first_key_at)                                    AS step_2_first_key,
    COUNT(ft.first_topup_at)                                  AS step_3_first_topup,
    COUNT(fr.first_request_at)                                AS step_4_first_request,
    COUNT(f5.fifth_request_at)                                AS step_5_five_requests,

    -- ---------- conversion rates % ----------
    ROUND(100.0 * COUNT(*) FILTER (WHERE c.email_verified) / NULLIF(COUNT(*),0), 1)
        AS conv_signup_to_verified_pct,
    ROUND(100.0 * COUNT(fk.first_key_at)      / NULLIF(COUNT(*) FILTER (WHERE c.email_verified),0), 1)
        AS conv_verified_to_key_pct,
    ROUND(100.0 * COUNT(ft.first_topup_at)    / NULLIF(COUNT(fk.first_key_at),0), 1)
        AS conv_key_to_topup_pct,
    ROUND(100.0 * COUNT(fr.first_request_at)  / NULLIF(COUNT(fk.first_key_at),0), 1)
        AS conv_key_to_first_request_pct,
    ROUND(100.0 * COUNT(f5.fifth_request_at)  / NULLIF(COUNT(fr.first_request_at),0), 1)
        AS conv_first_req_to_five_req_pct,

    -- ---------- median time-to-step (минуты) ----------
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (fk.first_key_at - c.account_created_at))/60.0
    ) AS median_min_signup_to_key,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (fr.first_request_at - fk.first_key_at))/60.0
    ) AS median_min_key_to_first_request,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (ft.first_topup_at - c.account_created_at))/3600.0
    ) AS median_hours_signup_to_topup
FROM cohort c
LEFT JOIN first_key     fk ON fk.account_id = c.account_id
LEFT JOIN first_topup   ft ON ft.account_id = c.account_id
LEFT JOIN first_request fr ON fr.account_id = c.account_id
LEFT JOIN fifth_request f5 ON f5.account_id = c.account_id;

-- =====================================================================
-- Вариант с разбивкой по тарифу (тот же CTE, GROUP BY tariff)
-- — раскомментируй когда платных >20 шт, до этого шум.
-- =====================================================================
-- ... GROUP BY c.tariff ORDER BY step_0_signed_up DESC;
