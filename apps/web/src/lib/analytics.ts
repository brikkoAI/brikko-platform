/**
 * Analytics — тонкая обёртка над `track(event, props)` для воронки signup→first_request.
 *
 * Sprint 12 §2.3 (29_post_launch_prd_2026-05-01.md): ICE 8.3, фича welcome-flow.
 *
 * Зачем stub, а не Posthog/Plausible сразу:
 *   - На 2026-05-02 SDK не подключен, ENV-переменных нет, выручки нет — платный
 *     Posthog заводить пока бессмысленно. Plausible/Yandex.Metrika зальём когда
 *     CEO решит (см. PROJECT_LOG).
 *   - Контракт `track(event, props)` совпадает с Posthog `posthog.capture(event, props)`
 *     и Plausible `plausible(event, { props })` — переключение в одну точку,
 *     остальной код переносится без правок.
 *   - Все вызовы из дашборда уже идут через этот модуль — будущая интеграция = одна
 *     инициализация в `src/app/providers.tsx` + `track()` начинает форвардить.
 *
 * Контр-аргументы:
 *   - Не считаем дубли (двойной `signup_completed` при F5 на /verify-email).
 *     Дубли решаются на стороне аналитики через distinct_id + idempotency_key,
 *     которые SDK добавляет сам. Stub их игнорирует.
 */

export type AnalyticsEvent =
  | 'signup_completed'
  | 'email_verified'
  | 'key_created'
  | 'first_request_made'
  | 'topup_completed'
  | 'playground_used'
  | 'playground_signup_cta_clicked'
  | 'playground_limit_hit';

export type AnalyticsProps = Record<string, string | number | boolean | null | undefined>;

/**
 * Идемпотентный флаг «событие уже отправлено в этой браузерной сессии» — чтобы
 * `signup_completed` не уходил повторно при каждом F5 на /app. Хранится в
 * `sessionStorage` (живёт ровно одну вкладку, очищается при close — это OK для
 * top-of-funnel событий).
 *
 * Для events типа `key_created` / `topup_completed` дедуп НЕ нужен — они уже
 * привязаны к успешной mutation (один pending-mutation = один track-call).
 */
const ONCE_PER_SESSION_EVENTS: ReadonlySet<AnalyticsEvent> = new Set([
  'signup_completed',
  'email_verified',
  'first_request_made',
]);

const SESSION_FLAG_PREFIX = 'brikko.analytics.fired:';

function alreadyFired(event: AnalyticsEvent): boolean {
  if (typeof window === 'undefined') return false;
  if (!ONCE_PER_SESSION_EVENTS.has(event)) return false;
  try {
    return window.sessionStorage.getItem(SESSION_FLAG_PREFIX + event) === '1';
  } catch {
    return false;
  }
}

function markFired(event: AnalyticsEvent): void {
  if (typeof window === 'undefined') return;
  if (!ONCE_PER_SESSION_EVENTS.has(event)) return;
  try {
    window.sessionStorage.setItem(SESSION_FLAG_PREFIX + event, '1');
  } catch {
    // privacy mode — теряем idempotency, не падаем.
  }
}

/**
 * Отправить event. SDK ещё не подключён — пишем в `window.__brikko_analytics_queue`
 * (массив). Когда инициализируем Posthog/Plausible — flushим очередь.
 *
 * В SSR — no-op (нечего трекать без браузера).
 * В Cypress/Playwright — ставит data-attr на body для assert'ов в тестах
 * (см. e2e/03_dashboard.spec.ts если понадобится).
 */
export function track(event: AnalyticsEvent, props: AnalyticsProps = {}): void {
  if (typeof window === 'undefined') return;
  if (alreadyFired(event)) return;

  const payload = {
    event,
    props: { ...props, ts: Date.now() },
  };

  type AnalyticsWindow = Window & {
    __brikko_analytics_queue?: Array<{ event: AnalyticsEvent; props: AnalyticsProps & { ts: number } }>;
  };
  const w = window as AnalyticsWindow;
  if (!w.__brikko_analytics_queue) w.__brikko_analytics_queue = [];
  w.__brikko_analytics_queue.push(payload);

  markFired(event);

  // Future: когда подключим Posthog —
  //   if (w.posthog) w.posthog.capture(event, props);
  // Future: Plausible —
  //   if (w.plausible) w.plausible(event, { props });
}
