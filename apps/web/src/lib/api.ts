import ky, { type KyInstance, HTTPError } from 'ky';
import type {
  Account,
  AccountClosureStatus,
  ActivityEvent,
  ApiErrorBody,
  ApiErrorType,
  ApiKey,
  ApiKeyCreated,
  ApiKeyScope,
  AuthSession,
  AutorefillSettings,
  AutorefillState,
  AutorefillUpdate,
  Balance,
  DataExportRequest,
  Kopecks,
  RoutingPreferences,
  RoutingPreferencesUpdate,
  LoginResponse,
  McpToken,
  McpTokenCreated,
  McpTokenScope,
  PendingInvite,
  Seat,
  SeatRole,
  SignupResponse,
  TariffChangeResponse,
  TariffSlug,
  TopupResponse,
  TransactionsPage,
  TransactionsQuery,
  TwoFactorSetupResponse,
  UsageGroupBy,
  UsageResponse,
  VerifyEmailResponse,
} from './types';

/**
 * Telegram link bootstrap response (Sprint 4 / Поток M).
 *
 * **Контракт (verified против OpenAPI 30.04 — TelegramLinkResponse component):**
 *   - `token`        — one-time token, single-use в боте.
 *   - `ttl_seconds`  — TTL токена (Поток M: 300 = 5 мин).
 *   - `bot_username` — имя бота (Поток M: 'VoltariBot') для построения deep-link.
 *   - `deep_link`    — pre-built `https://t.me/<bot>?start=<token>` (backend строит сам).
 *
 * UX-нота: `qr_url` мы решили **не делать** на этой итерации — QR можно построить на фронте
 * чистым CSS/SVG, если будет нужен (см. SettingsPage `TelegramLinkInstructions`); тащить
 * remote-image полнее, чем оно того стоит.
 */
export interface TelegramLinkResponse {
  token: string;
  ttl_seconds: number;
  bot_username: string;
  deep_link: string;
}

/**
 * Резолв base URL:
 *   1. NEXT_PUBLIC_API_BASE_URL непустой и не localhost:3000 → real backend.
 *   2. Иначе — '/api/mock/v1', который перехватит MSW worker (в браузере) или
 *      MSW server (в vitest).
 *
 * Дальше вся логика 401/402/429/5xx + CSRF-header + refresh-flow — общая,
 * MSW и real backend имеют одинаковый контракт ошибок (см. mocks/handlers.ts).
 */

const ENV_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const MOCK_BASE = '/api/mock/v1';

function resolveBaseUrl(): { url: string; isMock: boolean } {
  if (!ENV_BASE) return { url: MOCK_BASE, isMock: true };
  if (/localhost:3000/.test(ENV_BASE)) return { url: MOCK_BASE, isMock: true };
  return { url: ENV_BASE.replace(/\/$/, ''), isMock: false };
}

const { url: apiBaseUrl, isMock: USING_MOCKS } = resolveBaseUrl();

export const API_BASE_URL = apiBaseUrl;
export const IS_MOCK_BACKEND = USING_MOCKS;

// CSRF: backend требует header на всех мутациях (см. gateway/auth/session_middleware.py).
//
// **СТАТУС (FE P0-5 pre-work):** `lib/csrf.ts` хранит token и API готов к замене
// `X-Requested-With` → `X-CSRF-Token` ОДНИМ местом. Текущее поведение не меняется:
// мы шлём legacy-header, потому что backend пока проверяет именно его. Когда
// Поток D опубликует `docs/csrf_protocol.md` и переведёт middleware — здесь
// добавится `X-CSRF-Token` без перетряхивания всего api.ts.
import {
  getCsrfToken,
  setCsrfTokenFromResponse,
  clearCsrfToken,
  CSRF_HEADER_NAME,
  LEGACY_CSRF_HEADER_NAME,
  LEGACY_CSRF_VALUE,
} from './csrf';

const MUTATING_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

function isMutating(method: string | undefined): boolean {
  return Boolean(method && MUTATING_METHODS.has(method.toUpperCase()));
}

/**
 * Hook на 401: однократно редиректим на /login. Reason кодирует контекст:
 *   - `session_expired`: refresh-flow тоже вернул 401, кука была, но теперь null
 *   - `session_required`: cookie не было совсем (используется AppLayout server-side)
 *
 * Синхронный по дизайну: `window.location.assign` запустит навигацию и текущая
 * микротаска продолжится, но queryFn уже не успеет переотрисовать UI до новой
 * страницы. См. TD-032 — react-query retry на 401 не должен возвращать к этому
 * месту дважды.
 */
type LoginRedirectReason = 'session_expired' | 'session_required';

function redirectToLogin(reason: LoginRedirectReason = 'session_expired'): void {
  if (typeof window === 'undefined') return;
  const onLoginAdjacent = ['/login', '/signup', '/forgot-password', '/reset-password'].some((p) =>
    window.location.pathname.startsWith(p),
  );
  if (onLoginAdjacent) return;
  window.location.assign(`/login?reason=${reason}`);
}

// ----- Sentry: реальный @sentry/nextjs клиент. -------------------------------------------
// Динамический импорт нужен потому, что api.ts импортируется и из SSR, и из клиента,
// а Sentry разводит окружения через `sentry.client.config.ts` / `sentry.server.config.ts`.
// `Sentry.captureException` сам no-op'ит, если init не выполнился (например, DSN пуст) —
// см. sentry.client.config.ts.
async function captureServerError(err: unknown, context: { url?: string; status: number }): Promise<void> {
  try {
    const Sentry = await import('@sentry/nextjs');
    Sentry.captureException(err, {
      tags: { http_status: String(context.status) },
      extra: { url: context.url },
    });
  } catch {
    // Если SDK не загрузился (offline, ad-block) — теряем телеметрию, но не запрос.
  }
}

// ----- Toast (через sonner). Импорт ленивый, чтобы не тащить sonner на SSR ---------------
async function showToast(kind: 'warning' | 'error', message: string, action?: { label: string; onClick: () => void }): Promise<void> {
  if (typeof window === 'undefined') return;
  try {
    const { toast } = await import('sonner');
    if (kind === 'warning') toast.warning(message, action ? { action } : undefined);
    else toast.error(message, action ? { action } : undefined);
  } catch {
    // Sonner не загрузился — это не делает запрос менее проваленным.
  }
}

const client: KyInstance = ky.create({
  prefixUrl: apiBaseUrl,
  credentials: 'include',
  retry: {
    // GET-only retries (для idempotent-запросов). 401/refresh обрабатываем вручную ниже.
    limit: 2,
    methods: ['get'],
    statusCodes: [408, 429, 500, 502, 503, 504],
    backoffLimit: 3000,
  },
  timeout: 15_000,
  hooks: {
    beforeRequest: [
      async (req) => {
        req.headers.set('Accept', 'application/json');
        if (isMutating(req.method)) {
          // Архитектурно готовы к двойной отправке (legacy + новый header).
          // Сейчас getCsrfToken возвращает legacy-значение для совместимости с
          // текущим backend middleware. После выхода csrf_protocol.md — будет
          // выдавать настоящий per-session токен. См. lib/csrf.ts (FE P0-5).
          const token = await getCsrfToken();
          req.headers.set(LEGACY_CSRF_HEADER_NAME, LEGACY_CSRF_VALUE);
          req.headers.set(CSRF_HEADER_NAME, token);
        }
      },
    ],
  },
});

// ============================================================
// Refresh-token flow (single-flight)
// ============================================================
//
// Когда access JWT истекает (15 мин у backend'а) — клиент получает 401.
// Стратегия:
//   1. Однократно вызвать POST /auth/refresh (refresh cookie HttpOnly,
//      браузер пришлёт автоматически благодаря credentials: 'include').
//   2. Если refresh вернул 200 — повторить original request один раз.
//   3. Если refresh вернул 401 — это окончательно, redirect на /login.
//
// Single-flight через shared promise: если параллельно полетели 5 запросов
// и все получили 401, refresh случится один раз, остальные дождутся его результата.

let refreshInFlight: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  // Сбрасываем `refreshInFlight` СИНХРОННО внутри `.finally()`. Раньше использовался
  // `setTimeout(..., 0)`, что ломало single-flight-инвариант: между resolve и tick'ом
  // мог прийти параллельный 401 → второй refresh. Финки гарантируют, что любая
  // следующая микротаска видит сброс перед новой попыткой.
  refreshInFlight = (async () => {
    try {
      // Нельзя использовать `client.post()` — он сам триггернет refresh-loop при 401.
      // Используем чистый ky без afterResponse-hook'а; CSRF-headers ставим вручную.
      const csrfToken = await getCsrfToken();
      const res = await ky.post('auth/refresh', {
        prefixUrl: apiBaseUrl,
        credentials: 'include',
        headers: {
          [LEGACY_CSRF_HEADER_NAME]: LEGACY_CSRF_VALUE,
          [CSRF_HEADER_NAME]: csrfToken,
          Accept: 'application/json',
        },
        retry: 0,
        timeout: 10_000,
        throwHttpErrors: false,
      });
      if (res.ok) {
        // Backend ротирует CSRF token на каждый refresh (csrf_protocol.md §4).
        // Парсим body и обновляем кэш — следующий mutating-запрос пойдёт со свежим
        // токеном без лишнего GET /auth/csrf bootstrap'а.
        try {
          const body = (await res.json()) as { csrf_token?: string };
          if (body.csrf_token) {
            setCsrfTokenFromResponse({ csrf_token: body.csrf_token });
          }
        } catch {
          // Если backend временно не возвращает csrf_token в body — не критично,
          // следующий запрос триггернёт bootstrap через getCsrfToken().
        }
      }
      return res.ok;
    } catch {
      return false;
    }
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

// ============================================================
// Error class
// ============================================================

export class ApiClientError extends Error {
  public readonly type: ApiErrorType;
  public readonly status: number;
  public readonly retryAfterMs?: number;
  public readonly details?: Record<string, unknown>;
  /**
   * Backend's raw OpenAI-envelope code (e.g. `last_login_method`,
   * `email_taken`). Distinct from the frontend taxonomy in `type`,
   * which collapses many codes into a handful of categories. Use
   * `code` for fine-grained UX branches; use `type` for global
   * fallbacks.
   */
  public readonly code?: string;

  constructor(
    status: number,
    body: {
      type: ApiErrorType;
      message: string;
      retry_after_ms?: number;
      details?: Record<string, unknown>;
      code?: string;
    },
  ) {
    super(body.message);
    this.name = 'ApiClientError';
    this.type = body.type;
    this.status = status;
    this.retryAfterMs = body.retry_after_ms;
    this.details = body.details;
    this.code = body.code;
  }
}

// ============================================================
// Centralised request runner — оборачивает ky-вызов:
//   - 401 → refresh + retry (один раз).
//   - 402 / 429 / 5xx → toast + Sentry.
//   - Любые ошибки → ApiClientError с типизированным `type`.
// ============================================================

async function runRequest<T>(
  exec: () => Promise<T>,
  options?: { url?: string; alreadyRetried?: boolean },
): Promise<T> {
  try {
    return await exec();
  } catch (err) {
    if (err instanceof HTTPError) {
      const status = err.response.status;
      const url = err.response.url || options?.url;

      // ---- 401 → refresh-flow ----------------------------------------------
      // Не пытаемся рефрешить если URL = /auth/refresh или /auth/login (chicken-and-egg).
      // Логика веток:
      //   - первая 401, refresh не пробовали → пытаемся обновить токен и повторить.
      //   - refresh не помог ИЛИ второй 401 на том же запросе → синхронный redirect
      //     (а не "ждать пока tanstack-query решит retry'ить"). См. TD-032.
      // react-query retry на 401 уже отключен в QueryClient config (см. providers.tsx),
      // но мы дублируем синхронный redirect здесь, потому что imperative-вызовы
      // (вне useQuery) идут через тот же runRequest и должны вести себя одинаково.
      const isAuthBootstrap = /\/auth\/(refresh|login|signup|verify-email|forgot-password|reset-password)/.test(url ?? '');
      if (status === 401 && !isAuthBootstrap) {
        if (!options?.alreadyRetried) {
          const refreshed = await attemptRefresh();
          if (refreshed) {
            return runRequest(exec, { url, alreadyRetried: true });
          }
          // Refresh не помог — кука была, но теперь не валидна.
          redirectToLogin('session_expired');
        } else {
          // Второй 401 подряд после успешного refresh — backend отозвал сессию
          // прямо во время retry. Тоже session_expired — мы УЖЕ имели куку.
          redirectToLogin('session_expired');
        }
      }

      // ---- 403 csrf_invalid → один retry с ре-bootstrap токена ---------------
      // Возможные причины: TTL cookie истёк (30d), backend перезапустился и
      // отозвал token, пользователь open'ил вкладку которая spent неделю в фоне.
      // Контракт: clearCsrfToken() → next mutating request триггерит свежий
      // GET /v1/auth/csrf через getCsrfToken() в beforeRequest hook.
      // См. docs/csrf_protocol.md §4 (rotation) + §"Server-side error envelope".
      if (status === 403 && !options?.alreadyRetried && !isAuthBootstrap) {
        try {
          const errBody = (await err.response.clone().json()) as ApiErrorBody;
          if (errBody.error?.code === 'csrf_invalid') {
            clearCsrfToken();
            return runRequest(exec, { url, alreadyRetried: true });
          }
        } catch {
          // Body не JSON — это не csrf_invalid, продолжаем по обычному pathway.
        }
      }

      // ---- Парсинг тела ошибки --------------------------------------------
      const retryHeader = err.response.headers.get('retry-after');
      const retryHeaderMs = retryHeader ? Number(retryHeader) * 1000 : undefined;

      let body: ApiErrorBody = {
        error: { type: 'unknown_error', message: 'Неизвестная ошибка' },
      };
      try {
        // Backend (gateway) возвращает OpenAI-формат: { error: { type, message, code, param } }.
        // Наши моки — тот же формат + retry_after_ms на 429. Оба совместимы.
        body = (await err.response.json()) as ApiErrorBody;
      } catch {
        // Пустой/HTML body (ЮKassa redirect, gateway timeout) — оставляем дефолт.
      }
      const type = mapErrorType(status, body.error?.type, body.error?.code);
      const message = body.error?.message ?? 'Неизвестная ошибка';
      const retryAfterMs = body.error?.retry_after_ms ?? retryHeaderMs;
      const apiErr = new ApiClientError(status, {
        type,
        message,
        retry_after_ms: retryAfterMs,
        details: body.error?.details,
        code: body.error?.code,
      });

      // ---- Side effects: toast + Sentry (mutations only, чтобы не дублировать
      //      ту же ошибку в разных местах при параллельных GET'ах). ----
      // Single point of truth — в providers.tsx тоже стоит mutation onError, но
      // он не покрывает GET'ы fail на /balance. Здесь — нижний уровень.
      if (status === 402) {
        // §5.1 — Insufficient balance. Если backend прислал детальный message —
        // используем его (там обычно числа), иначе fallback из R-vetted доки.
        void showToast(
          'warning',
          message || 'На балансе 0 ₽ — запросы остановлены. Пополнить баланс, чтобы продолжить.',
          {
            label: 'Пополнить',
            onClick: () => window.location.assign('/app/billing'),
          },
        );
      } else if (status === 429) {
        // §5.4 — Rate limit. Дословно из dashboard_states_and_flows.md §5.4.
        const sec = Math.max(1, Math.ceil((retryAfterMs ?? 1500) / 1000));
        void showToast('warning', `Превышен лимит запросов. Повторить через ${sec} секунд.`);
      } else if (status >= 500) {
        void captureServerError(apiErr, { url, status });
        void showToast('error', 'Что-то у нас сломалось. Мы уже знаем — попробуй обновить страницу.');
      }

      throw apiErr;
    }
    if (err instanceof Error) {
      throw new ApiClientError(0, { type: 'network_error', message: err.message });
    }
    throw new ApiClientError(0, { type: 'unknown_error', message: 'Неизвестная ошибка' });
  }
}

/**
 * Backend gateway отдаёт OpenAI-style envelope (type/code/param) — мы маппим
 * это на frontend-таксономию ApiErrorType. Моки используют ту же таксономию
 * напрямую, поэтому если type уже подходит — отдаём его как есть.
 */
function mapErrorType(status: number, rawType?: string, code?: string): ApiErrorType {
  if (rawType && KNOWN_ERROR_TYPES.has(rawType as ApiErrorType)) {
    return rawType as ApiErrorType;
  }
  // Backend OpenAI-style codes:
  if (code === 'rate_limited') return 'rate_limit';
  if (code === 'email_taken') return 'email_already_registered';
  if (code === 'invalid_token' || code === 'token_expired') return 'token_expired';
  if (code === 'invalid_password' || code === 'invalid_credentials') return 'invalid_credentials';
  if (code === 'csrf_required' || code === 'forbidden') return 'forbidden';
  if (code === 'insufficient_balance' || code === 'insufficient_quota') return 'insufficient_balance';
  if (
    code === 'seat_limit_reached' ||
    code === 'key_limit_reached' ||
    code === 'mcp_token_limit_reached'
  ) {
    return code as ApiErrorType;
  }
  // Status fallbacks:
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'email_already_registered';
  if (status === 422 || status === 400) return 'validation_error';
  if (status === 429) return 'rate_limit';
  if (status >= 500) return 'server_error';
  return 'unknown_error';
}

const KNOWN_ERROR_TYPES = new Set<ApiErrorType>([
  'validation_error',
  'invalid_credentials',
  'email_already_registered',
  'unauthorized',
  'forbidden',
  'not_found',
  'rate_limit',
  'insufficient_balance',
  'token_expired',
  'invite_already_member',
  'seat_limit_reached',
  'key_limit_reached',
  'mcp_token_limit_reached',
  'server_error',
  'network_error',
  'unknown_error',
]);

// ============================================================
// Auth
// ============================================================

export interface SignupPayload {
  email: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export const authApi = {
  signup: (payload: SignupPayload) =>
    runRequest(() => client.post('auth/signup', { json: payload }).json<SignupResponse>(), { url: 'auth/signup' }),
  login: (payload: LoginPayload) =>
    runRequest(() => client.post('auth/login', { json: payload }).json<LoginResponse>(), { url: 'auth/login' }),
  logout: () => runRequest(() => client.post('auth/logout').json<{ ok: true }>(), { url: 'auth/logout' }),
  verifyEmail: (token: string) =>
    runRequest(
      () => client.post('auth/verify-email', { json: { token } }).json<VerifyEmailResponse>(),
      { url: 'auth/verify-email' },
    ),
  forgotPassword: (email: string) =>
    runRequest(() => client.post('auth/forgot-password', { json: { email } }).json<{ ok: true }>(), {
      url: 'auth/forgot-password',
    }),
  resetPassword: (token: string, new_password: string) =>
    runRequest(
      () => client.post('auth/reset-password', { json: { token, new_password } }).json<{ ok: true }>(),
      { url: 'auth/reset-password' },
    ),
  changePassword: (old_password: string, new_password: string) =>
    runRequest(
      () =>
        client
          // Backend ожидает поля `old_password` и `new_password` (см. ChangePasswordRequest в api/auth.py).
          .post('auth/change-password', { json: { old_password, new_password } })
          .json<{ ok: true }>(),
      { url: 'auth/change-password' },
    ),
  resendVerification: (email: string) =>
    runRequest(
      () => client.post('auth/resend-verification', { json: { email } }).json<{ ok: true }>(),
      { url: 'auth/resend-verification' },
    ),
  refresh: () =>
    runRequest(() => client.post('auth/refresh').json<LoginResponse>(), { url: 'auth/refresh' }),

  // ============================================================
  // Sprint 6: email-verification resend (для expired-state на /verify-email)
  // ============================================================
  emailVerifyResend: (email: string) =>
    runRequest(
      () => client.post('auth/email-verify/resend', { json: { email } }).json<{ ok: true }>(),
      { url: 'auth/email-verify/resend' },
    ),

  // ============================================================
  // Sprint 6: 2FA — TOTP-based (Google Authenticator / Authy / 1Password)
  // ============================================================
  /**
   * Шаг 1: запросить secret + QR.
   * Backend генерирует secret, recovery codes, QR; ничего не активирует пока verify не пришёл.
   */
  twoFactorSetup: () =>
    runRequest(
      () => client.post('auth/2fa/setup').json<TwoFactorSetupResponse>(),
      { url: 'auth/2fa/setup' },
    ),
  /** Шаг 2: подтвердить кодом из app — активирует 2FA. */
  twoFactorVerify: (code: string) =>
    runRequest(
      () => client.post('auth/2fa/verify', { json: { code } }).json<{ ok: true }>(),
      { url: 'auth/2fa/verify' },
    ),
  /** Отключить 2FA. Требует свежий TOTP code (защита от компрометации сессии). */
  twoFactorDisable: (code: string) =>
    runRequest(
      () => client.post('auth/2fa/disable', { json: { code } }).json<{ ok: true }>(),
      { url: 'auth/2fa/disable' },
    ),

  // ============================================================
  // Sprint 6: смена пароля внутри Settings (отличается от /auth/change-password —
  // это требует current_password / new_password — контракт ровно как в спеке Sprint 6).
  // ============================================================
  passwordChangeV2: (current_password: string, new_password: string) =>
    runRequest(
      () =>
        client
          .post('auth/password/change', { json: { current_password, new_password } })
          .json<{ ok: true }>(),
      { url: 'auth/password/change' },
    ),

  // ============================================================
  // Sprint 6: активные сессии — текущая + revoke
  // ============================================================
  listSessions: () =>
    // Gateway returns `{items: AuthSession[]}` (SessionsListResponse). The
    // hook layer expects a plain array — unwrap here so all callers see
    // the same shape and stale code can't crash on `.filter` (was the
    // 2026-05-08 ErrorBoundary on /app/settings/security).
    runRequest(
      () =>
        client
          .get('auth/sessions')
          .json<{ items: AuthSession[] }>()
          .then((r) => r.items ?? []),
      { url: 'auth/sessions' },
    ),
  revokeSession: (id: string) =>
    runRequest(() => client.delete(`auth/sessions/${id}`).text(), { url: `auth/sessions/${id}` }).then(
      () => ({ ok: true as const }),
    ),
  revokeAllSessions: () =>
    runRequest(() => client.delete('auth/sessions').text(), { url: 'auth/sessions' }).then(
      () => ({ ok: true as const }),
    ),

  // ============================================================
  // OAuth login / link / disconnect (Google + Yandex)
  // ============================================================
  /** Список привязанных OAuth-провайдеров. Используется в Settings → Безопасность. */
  listOAuthIdentities: () =>
    runRequest(
      () => client.get('auth/oauth/identities').json<{ identities: OAuthIdentity[] }>(),
      { url: 'auth/oauth/identities' },
    ),
  /** Удалить связку. Backend блокирует, если это последний способ входа. */
  disconnectOAuth: (provider: 'google' | 'yandex') =>
    runRequest(
      () => client.post('auth/oauth/disconnect', { json: { provider } }).json<{ ok: true }>(),
      { url: 'auth/oauth/disconnect' },
    ),
};

export interface OAuthIdentity {
  provider: 'google' | 'yandex';
  email_at_link: string | null;
  email_verified_at_link: boolean;
  display_name: string | null;
  avatar_url: string | null;
  linked_via: 'signup' | 'settings' | 'email_match';
  linked_at: string;
  last_login_at: string | null;
}

// ============================================================
// Account
// ============================================================

export const accountApi = {
  me: () => runRequest(() => client.get('account').json<Account>(), { url: 'account' }),
  updateProfile: (payload: { name?: string; email?: string }) =>
    runRequest(() => client.patch('account/profile', { json: payload }).json<Account>(), {
      url: 'account/profile',
    }),
  updateSettings: (payload: {
    prompt_logging_enabled?: boolean;
    /**
     * Sprint 4 / Поток M. Backend расширяет PATCH /v1/account/settings.
     * Если backend ещё не выкатил поддержку — endpoint вернёт 200 без изменения,
     * но и без `pii_masking_enabled` в response. UI трактует это как «фича пока недоступна»
     * через optional типизацию.
     */
    pii_masking_enabled?: boolean;
    notifications?: Record<string, unknown>;
  }) =>
    runRequest(() => client.patch('account/settings', { json: payload }).json<Account>(), {
      url: 'account/settings',
    }),
  /**
   * Telegram-бот integration (Sprint 4 / Поток M).
   *
   * `linkTelegram` — генерирует one-time deep-link token (TTL 5 мин).
   * Backend возвращает `{ token, deep_link, qr_url, expires_at }`.
   * Если backend ещё не готов — UI получит 404 и покажет «Поток M ещё не выкатил endpoint».
   */
  linkTelegram: () =>
    runRequest(() => client.post('account/telegram-link').json<TelegramLinkResponse>(), {
      url: 'account/telegram-link',
    }),
  unlinkTelegram: () =>
    runRequest(() => client.delete('account/telegram-link').text(), {
      url: 'account/telegram-link',
    }).then(() => ({ ok: true as const })),
  seats: () => runRequest(() => client.get('account/seats').json<Seat[]>(), { url: 'account/seats' }),
  invites: () =>
    runRequest(() => client.get('account/invites').json<PendingInvite[]>(), { url: 'account/invites' }),
  inviteSeat: (payload: { email: string; role: SeatRole }) =>
    runRequest(
      () => client.post('account/seats/invite', { json: payload }).json<PendingInvite>(),
      { url: 'account/seats/invite' },
    ),
  removeSeat: (user_id: string) =>
    runRequest(() => client.delete(`account/seats/${user_id}`).json<{ ok: true }>(), {
      url: `account/seats/${user_id}`,
    }),
  cancelInvite: (invite_id: string) =>
    runRequest(() => client.delete(`account/invites/${invite_id}`).json<{ ok: true }>(), {
      url: `account/invites/${invite_id}`,
    }),

  // ============================================================
  // Sprint 6: смена тарифа.
  //
  // Backend списывает первое-месячное-значение с баланса (предоплата). На 402 —
  // фронт открывает «Недостаточно средств» в UpgradeConfirmModal.
  // ============================================================
  changeTariff: (tariff: TariffSlug) =>
    runRequest(
      () => client.patch('account/tariff', { json: { tariff } }).json<TariffChangeResponse>(),
      { url: 'account/tariff' },
    ),

  // ============================================================
  // Sprint 6: data-export (152-ФЗ + GDPR-style — запрос полного дампа).
  //
  // 202 Accepted — backend асинхронно собирает архив; ссылка приходит на email.
  // 429 — за последние 24 часа уже запрашивали.
  // ============================================================
  requestDataExport: () =>
    runRequest(() => client.post('account/data-export').json<DataExportRequest>(), {
      url: 'account/data-export',
    }),
  /** Последний запрос (для отображения «уже запрошено X часов назад»). */
  latestDataExport: () =>
    runRequest(
      () => client.get('account/data-export/latest').json<DataExportRequest | null>(),
      { url: 'account/data-export/latest' },
    ),
  /**
   * Sprint 7: список всех экспортов аккаунта (для История экспортов).
   * Backend сортирует по requested_at DESC. Лимит ~10-20 — больше не имеет смысла,
   * presigned URL'ы старше 30 дней backend помечает как expired.
   */
  listDataExports: () =>
    runRequest(
      () => client.get('account/data-export').json<DataExportRequest[]>(),
      { url: 'account/data-export' },
    ),
  /**
   * Sprint 7: статус конкретного экспорта (для polling'а).
   *
   * UX-нота: используется внутри useDataExportStatus с exponential backoff
   * (5s первые 30s → cap 30s) — иначе при долгой обработке (>10 мин) забили бы
   * сервер на 720 req/час с одного клиента.
   */
  dataExportStatus: (id: string) =>
    runRequest(
      () => client.get(`account/data-export/${encodeURIComponent(id)}`).json<DataExportRequest>(),
      { url: `account/data-export/${id}` },
    ),

  // ============================================================
  // Sprint 6: закрытие аккаунта.
  //
  // POST: schedule (через 30 дней). DELETE: отменить запланированное закрытие.
  // ============================================================
  closeAccount: (reason?: string) =>
    runRequest(
      () => client.post('account/close', { json: reason ? { reason } : {} }).json<AccountClosureStatus>(),
      { url: 'account/close' },
    ),
  cancelClosure: () =>
    runRequest(() => client.delete('account/close').json<AccountClosureStatus>(), {
      url: 'account/close',
    }),

  // ============================================================
  // Sprint 7: routing preferences (per-account smart-router policy).
  //
  // Спека: 02_Product/v1.5/22_routing_preferences_spec.md.
  // TODO: regen from openapi after BE Sprint 7 merge.
  // ============================================================
  routingPreferences: () =>
    runRequest(
      () => client.get('account/routing-preferences').json<RoutingPreferences>(),
      { url: 'account/routing-preferences' },
    ),
  updateRoutingPreferences: (payload: RoutingPreferencesUpdate) =>
    runRequest(
      () =>
        client
          .put('account/routing-preferences', { json: payload })
          .json<RoutingPreferences>(),
      { url: 'account/routing-preferences' },
    ),

  // ============================================================
  // Sprint 8: Auto-refill v2 (per-account, multi-card support).
  //
  // Спека: BRIEF Sprint 8 §1.
  // - GET    /v1/account/autorefill            → AutorefillState
  // - PUT    /v1/account/autorefill            → AutorefillState (full replace)
  // - POST   /v1/account/autorefill/disable    → AutorefillState с enabled=false
  //
  // Контракт обсуждался с BE: PUT — full replace (а не PATCH), чтобы фронт всегда
  // знал точное состояние, а не угадывал через optional-поля. POST .../disable —
  // отдельный endpoint, чтобы аудит-лог backend'а легче трекал отключения.
  //
  // TODO: regen from openapi after BE Sprint 8 merge.
  // ============================================================
  getAutorefill: () =>
    runRequest(() => client.get('account/autorefill').json<AutorefillState>(), {
      url: 'account/autorefill',
    }),
  updateAutorefill: (payload: AutorefillUpdate) =>
    runRequest(
      () => client.put('account/autorefill', { json: payload }).json<AutorefillState>(),
      { url: 'account/autorefill' },
    ),
  disableAutorefill: () =>
    runRequest(
      () => client.post('account/autorefill/disable').json<AutorefillState>(),
      { url: 'account/autorefill/disable' },
    ),

  // ============================================================
  // Sprint 8: Activity feed (последние события аккаунта).
  //
  // Спека: BRIEF Sprint 8 §4. Backend Sprint 8 будет писать события
  // в Account.activity_log при ключевых mutations.
  // ============================================================
  activity: (params: { limit?: number } = {}) =>
    runRequest(
      async () => {
        const res = await client
          .get('account/activity', {
            searchParams: params.limit ? { limit: params.limit } : {},
          })
          .json<{ items: ActivityEvent[]; limit: number } | ActivityEvent[]>();
        return Array.isArray(res) ? res : res.items;
      },
      { url: 'account/activity' },
    ),
};

// ============================================================
// API keys
// ============================================================

/**
 * Backend принимает/возвращает scope как 'read'|'write' (см. gateway/api/keys.py:103).
 * Frontend оперирует UX-friendly literals 'full'|'read_only'. Маппинг в обе стороны
 * чтобы UI-код оставался декларативным и type-safe.
 *
 * Без обратного маппинга key.scope в response остаётся в backend-форме ('write'),
 * а тип ApiKeyScope требует 'full'|'read_only' — keys/page.tsx сравнивал с UX-литералом
 * и всегда выводил "Только чтение" для всех ключей.
 */
function uiScopeToApi(scope: ApiKeyScope): 'read' | 'write' {
  return scope === 'read_only' ? 'read' : 'write';
}

function apiScopeToUi(scope: 'read' | 'write' | string): ApiKeyScope {
  return scope === 'read' ? 'read_only' : 'full';
}

interface ApiKeyBackendShape extends Omit<ApiKey, 'scope'> {
  scope: 'read' | 'write';
}

interface ApiKeyCreatedBackendShape extends Omit<ApiKeyCreated, 'scope'> {
  scope: 'read' | 'write';
}

function transformKey(k: ApiKeyBackendShape): ApiKey {
  return { ...k, scope: apiScopeToUi(k.scope) };
}

function transformKeyCreated(k: ApiKeyCreatedBackendShape): ApiKeyCreated {
  return { ...k, scope: apiScopeToUi(k.scope) };
}

export const keysApi = {
  list: () =>
    runRequest(
      () => client.get('keys').json<ApiKeyBackendShape[]>().then((arr) => arr.map(transformKey)),
      { url: 'keys' },
    ),
  create: (payload: { name: string; scope: ApiKeyScope }) =>
    runRequest(
      () =>
        client
          .post('keys', { json: { name: payload.name, scope: uiScopeToApi(payload.scope) } })
          .json<ApiKeyCreatedBackendShape>()
          .then(transformKeyCreated),
      { url: 'keys' },
    ),
  rename: (id: string, name: string) =>
    runRequest(
      () =>
        client
          .patch(`keys/${id}`, { json: { name } })
          .json<ApiKeyBackendShape>()
          .then(transformKey),
      { url: `keys/${id}` },
    ),
  revoke: (id: string) =>
    runRequest(() => client.delete(`keys/${id}`).text(), { url: `keys/${id}` }).then(
      () => ({ ok: true as const }),
    ),

  // ============================================================
  // Sprint 8: Bulk operations (revoke / rotate)
  //
  // Спека: BRIEF Sprint 8 §5. Backend Sprint 8 атомарно проводит операцию по
  // списку ID (либо все, либо никто — partial-failures возвращают 207 с per-id
  // статусом, но в MVP считаем 207 = error и просим повторить).
  //
  // Контракт ответа на success:
  //   {revoked: string[]}            — для bulk-revoke
  //   {rotated: {id: string; full_key: string; prefix: string}[]} — для bulk-rotate
  //
  // TODO: regen from openapi after BE Sprint 8 merge.
  // ============================================================
  bulkRevoke: (key_ids: string[]) =>
    runRequest(
      () =>
        client
          .post('keys/bulk-revoke', { json: { key_ids } })
          .json<{ revoked: string[] }>(),
      { url: 'keys/bulk-revoke' },
    ),
  bulkRotate: (key_ids: string[]) =>
    runRequest(
      () =>
        client
          .post('keys/bulk-rotate', { json: { key_ids } })
          .json<{
            rotated: Array<{
              id: string;
              full_key: string;
              prefix: string;
              scope: 'read' | 'write';
            }>;
          }>()
          .then((res) => ({
            rotated: res.rotated.map((r) => ({
              ...r,
              scope: apiScopeToUi(r.scope),
            })),
          })),
      { url: 'keys/bulk-rotate' },
    ),
};

// ============================================================
// MCP tokens (Sprint MCP S1)
//
// Backend: voltari_gateway/api/mcp_keys.py — mounted /v1/mcp/tokens.
// Scope literals совпадают со server-side enum (read_account|read_usage|
// recommend_model), маппинг не нужен. Bulk-операций нет в S1 (KISS).
// ============================================================

export const mcpKeysApi = {
  list: () =>
    runRequest(() => client.get('mcp/tokens').json<McpToken[]>(), { url: 'mcp/tokens' }),
  create: (payload: {
    name: string;
    scope: McpTokenScope;
    expires_in_days?: 30 | 90 | 180 | 365;
  }) =>
    runRequest(
      () =>
        client
          .post('mcp/tokens', { json: payload })
          .json<McpTokenCreated>(),
      { url: 'mcp/tokens' },
    ),
  rename: (id: string, name: string) =>
    runRequest(
      () => client.patch(`mcp/tokens/${id}`, { json: { name } }).json<McpToken>(),
      { url: `mcp/tokens/${id}` },
    ),
  revoke: (id: string) =>
    runRequest(() => client.delete(`mcp/tokens/${id}`).text(), {
      url: `mcp/tokens/${id}`,
    }).then(() => ({ ok: true as const })),
};

// ============================================================
// Billing
// ============================================================

export const billingApi = {
  balance: () =>
    runRequest(() => client.get('billing/balance').json<Balance>(), { url: 'billing/balance' }),
  topup: (payload: {
    amount_rub: number;
    return_url: string;
    // Pre-select payment method to skip ЮKassa's own picker page.
    // Omit to keep legacy behaviour (ЮKassa shows all approved methods).
    // Approved on shop 1345959 (2026-05-08): bank_card, sbp,
    // tinkoff_bank (T-Pay), sberbank (SberPay).
    payment_method?: 'bank_card' | 'sbp' | 'tinkoff_bank' | 'sberbank';
  }) =>
    runRequest(() => client.post('billing/topup', { json: payload }).json<TopupResponse>(), {
      url: 'billing/topup',
    }),
  transactions: (params: TransactionsQuery = {}) =>
    runRequest(
      () => {
        // ky поддерживает массивы в searchParams, но повторяемые имена лучше
        // строить вручную через URLSearchParams — иначе на старом backend'е
        // (который понимает только ?kind=topup, без [], без CSV) будет несовместимо.
        const usp = new URLSearchParams();
        if (params.from) usp.set('from', params.from);
        if (params.to) usp.set('to', params.to);
        if (params.search) usp.set('search', params.search);
        if (params.min_amount !== undefined) usp.set('min_amount', String(params.min_amount));
        if (params.max_amount !== undefined) usp.set('max_amount', String(params.max_amount));
        if (params.limit !== undefined) usp.set('limit', String(params.limit));
        if (params.offset !== undefined) usp.set('offset', String(params.offset));
        if (params.kind) {
          for (const k of params.kind) usp.append('kind', k);
        }
        return client
          .get('billing/transactions', { searchParams: usp })
          .json<TransactionsPage>();
      },
      { url: 'billing/transactions' },
    ),
  receiptUrl: (transaction_id: string) =>
    `${apiBaseUrl}/billing/receipts/${encodeURIComponent(transaction_id)}`,
  /**
   * Comply Pack URLs (Sprint 4 / Поток M).
   *
   * Backend генерирует PDF on-demand и отдаёт через стрим. Используем абсолютный URL +
   * `<a target="_blank">`, потому что:
   *   1. PDF — большой бинарь, через ky не качаем (нет смысла гонять через JS-промежуточную
   *      обработку в Blob).
   *   2. Browser использует built-in PDF viewer — UX лучше нашего самопального preview.
   *
   * **Контракт (Поток M):**
   *   - GET /v1/billing/documents/{tx_id}/akt → PDF (для usage/topup транзакций ≥1000₽).
   *   - GET /v1/billing/documents/upd?period_from=YYYY-MM-DD&period_to=YYYY-MM-DD → PDF (УПД за период).
   *   - GET /v1/billing/documents/invoice?period=YYYY-MM → PDF (счёт за месяц).
   *   - GET /v1/billing/documents/summary?from=...&to=... → PDF (сводный отчёт).
   *
   * Авторизация — session cookie (как у `receiptUrl`). Если backend ещё не готов —
   * получим 404, browser покажет ошибку. UI скрывает ссылки до того, как Поток M подтвердит.
   */
  aktUrl: (transaction_id: string) =>
    `${apiBaseUrl}/billing/documents/${encodeURIComponent(transaction_id)}/akt`,
  updUrl: (params: { period_from: string; period_to: string }) =>
    `${apiBaseUrl}/billing/documents/upd?period_from=${encodeURIComponent(params.period_from)}&period_to=${encodeURIComponent(params.period_to)}`,
  invoiceUrl: (period: string) =>
    `${apiBaseUrl}/billing/documents/invoice?period=${encodeURIComponent(period)}`,
  summaryUrl: (params: { from: string; to: string }) =>
    `${apiBaseUrl}/billing/documents/summary?from=${encodeURIComponent(params.from)}&to=${encodeURIComponent(params.to)}`,
  enableAutorefill: (payload: AutorefillSettings) =>
    runRequest(() => client.post('billing/autorefill', { json: payload }).json<{ ok: true }>(), {
      url: 'billing/autorefill',
    }),
  disableAutorefill: () =>
    runRequest(() => client.delete('billing/autorefill').text(), { url: 'billing/autorefill' }).then(
      () => ({ ok: true as const }),
    ),
};

// ============================================================
// Usage
// ============================================================

export const usageApi = {
  query: (params: { from: string; to: string; group_by: UsageGroupBy }) =>
    runRequest(
      () =>
        client
          .get('usage', {
            // ky `searchParams` принимает `Record<string, string | number | boolean>`,
            // но literal-union `UsageGroupBy` ('day'|'model'|'key') не структурно
            // присваивается `string` без widening. Spread-копия даёт нам widening
            // через явный тип переменной — без any/unknown-кастов.
            searchParams: {
              from: params.from,
              to: params.to,
              group_by: params.group_by,
            },
          })
          .json<UsageResponse>(),
      { url: 'usage' },
    ),
};

// ============================================================
// BrikkoLens — observability traces (Phase 5 #4, 2026-05-09)
// ============================================================

export interface TraceListItem {
  id: string;
  request_id: string;
  provider: string;
  model: string;
  routed_from: string | null;
  started_at: string;
  finished_at: string;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_kop: number;
  status: 'ok' | 'error' | 'timeout' | 'cache_hit' | 'rate_limited';
  http_code: number | null;
  error_code: string | null;
  is_streaming: boolean;
  cache_hit: boolean;
  tools_used: boolean;
  pii_masked: boolean;
}

export interface TracesListResponse {
  items: TraceListItem[];
  total: number;
  has_more: boolean;
}

export interface TraceDetail extends TraceListItem {
  api_key_id: string | null;
  error_message: string | null;
  reasoning_tokens: number;
  fx_usd_rub: number | null;
  request_body: Record<string, unknown> | null;
  response_body: Record<string, unknown> | null;
  model_params: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
}

export interface TracesQueryParams {
  limit?: number;
  offset?: number;
  status?: TraceListItem['status'];
  model?: string;
  provider?: string;
  api_key_id?: string;
  since?: string; // ISO8601
  until?: string;
  min_cost_kop?: number;
  max_cost_kop?: number;
  min_latency_ms?: number;
  max_latency_ms?: number;
  only_with_tools?: boolean;
  only_with_cache?: boolean;
  q?: string;
}

export const tracesApi = {
  list: (params: TracesQueryParams = {}) =>
    runRequest(
      () => {
        const searchParams: Record<string, string | number | boolean> = {};
        if (params.limit !== undefined) searchParams.limit = params.limit;
        if (params.offset !== undefined) searchParams.offset = params.offset;
        if (params.status) searchParams.status = params.status;
        if (params.model) searchParams.model = params.model;
        if (params.provider) searchParams.provider = params.provider;
        if (params.api_key_id) searchParams.api_key_id = params.api_key_id;
        if (params.since) searchParams.since = params.since;
        if (params.until) searchParams.until = params.until;
        if (params.min_cost_kop !== undefined)
          searchParams.min_cost_kop = params.min_cost_kop;
        if (params.max_cost_kop !== undefined)
          searchParams.max_cost_kop = params.max_cost_kop;
        if (params.min_latency_ms !== undefined)
          searchParams.min_latency_ms = params.min_latency_ms;
        if (params.max_latency_ms !== undefined)
          searchParams.max_latency_ms = params.max_latency_ms;
        if (params.only_with_tools) searchParams.only_with_tools = true;
        if (params.only_with_cache) searchParams.only_with_cache = true;
        if (params.q) searchParams.q = params.q;
        return client.get('account/traces', { searchParams }).json<TracesListResponse>();
      },
      { url: 'account/traces' },
    ),
  detail: (request_id: string) =>
    runRequest(
      () => client.get(`account/traces/${encodeURIComponent(request_id)}`).json<TraceDetail>(),
      { url: `account/traces/${request_id}` },
    ),
};

// ============================================================
// BrikkoLens — analytics summary (Sprint 2, 2026-05-09)
// ============================================================

export interface AnalyticsTotals {
  requests: number;
  errors: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_kop: number;
  cache_hits: number;
  tool_calls: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
}

export interface AnalyticsDaily {
  day: string; // YYYY-MM-DD
  requests: number;
  errors: number;
  cost_kop: number;
  prompt_tokens: number;
  completion_tokens: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
}

export interface AnalyticsBreakdownItem {
  key: string;
  requests: number;
  cost_kop: number;
  share: number;
}

export interface AnalyticsSummaryResponse {
  range_from: string;
  range_to: string;
  totals: AnalyticsTotals;
  daily: AnalyticsDaily[];
  by_model: AnalyticsBreakdownItem[];
  by_provider: AnalyticsBreakdownItem[];
  by_status: AnalyticsBreakdownItem[];
}

export interface AnalyticsSummaryParams {
  from?: string; // YYYY-MM-DD
  to?: string;
}

export const analyticsApi = {
  summary: (params: AnalyticsSummaryParams = {}) =>
    runRequest(
      () => {
        const searchParams: Record<string, string> = {};
        if (params.from) searchParams.from = params.from;
        if (params.to) searchParams.to = params.to;
        return client
          .get('account/analytics/summary', { searchParams })
          .json<AnalyticsSummaryResponse>();
      },
      { url: 'account/analytics/summary' },
    ),
};

// ============================================================
// Platform admin status — single-pane CEO dashboard (Sprint 13.7)
// ============================================================

export interface AdminProviderHealth {
  name: string;
  configured: boolean;
  status: 'ok' | 'error' | 'unconfigured';
}

export interface AdminLast24h {
  requests: number;
  errors: number;
  error_rate: number;
  cost_kop: number;
  active_accounts: number;
  avg_latency_ms: number;
}

export interface AdminDatabaseStats {
  accounts_total: number;
  users_total: number;
  traces_total: number;
  traces_24h: number;
}

export interface AdminDeployInfo {
  sha: string | null;
  deployed_at: string | null;
  version: string;
}

export interface AdminStatusResponse {
  api_status: 'ok' | 'degraded' | 'down';
  timestamp: string;
  last_24h: AdminLast24h;
  providers: AdminProviderHealth[];
  database: AdminDatabaseStats;
  deploy: AdminDeployInfo;
}

export interface AdminCheckResponse {
  is_admin: boolean;
}

// ============================================================
// Provider balances — backend контракт описан в BRIEF админ-задачи.
// `balance_native` хранится в native unit'ах провайдера (USD у OpenAI,
// RUB у Sber/Yandex, CNY у китайских, tokens где предоплатные пакеты).
// `balance_rub_kopecks` — нормализованное значение в копейках для
// сортировки/сравнения; backend конвертирует через курс ЦБ.
// ============================================================

export type ProviderKey =
  | 'openai'
  | 'anthropic'
  | 'deepseek'
  | 'sber'
  | 'moonshot'
  | 'minimax'
  | 'zhipu'
  | 'together'
  | 'yandex'
  | 'google';

export type BalanceCurrency = 'USD' | 'RUB' | 'CNY' | 'EUR' | 'tokens';

export type FetchStatus = 'ok' | 'error' | 'manual' | 'pending';
export type FetchMethod = 'api' | 'manual' | 'scrape';

export interface ProviderBalance {
  provider: ProviderKey;
  balance_native: number | null;
  balance_currency: BalanceCurrency | null;
  balance_rub_kopecks: number | null;
  last_fetched_at: string;
  last_success_at: string | null;
  fetch_status: FetchStatus;
  fetch_method: FetchMethod;
  error_message: string | null;
  burn_rate_kopecks_per_day: number | null;
  runway_days: number | null;
  notes: string | null;
}

export interface GetProviderBalancesResponse {
  items: ProviderBalance[];
}

export interface ManualBalanceUpdate {
  balance_native: number;
  currency: BalanceCurrency;
  notes?: string;
}

/** Providers served by the Playwright scraper (Phase 2). */
export type ScrapeProviderKey = 'openai' | 'anthropic' | 'together';

export interface ProviderCookieMeta {
  provider: ScrapeProviderKey;
  uploaded_at: string | null;
  last_valid_at: string | null;
  last_error: string | null;
  size_bytes: number | null;
  has_cookies: boolean;
}

export interface GetProviderCookiesResponse {
  items: ProviderCookieMeta[];
}

export interface ProviderCookieUploadResponse {
  provider: ScrapeProviderKey;
  uploaded_at: string;
  size_bytes: number;
}

export const adminApi = {
  status: () =>
    runRequest(
      () => client.get('account/admin/status').json<AdminStatusResponse>(),
      { url: 'account/admin/status' },
    ),
  check: () =>
    runRequest(
      () => client.get('account/me/admin-check').json<AdminCheckResponse>(),
      { url: 'account/me/admin-check' },
    ),

  // ============================================================
  // Provider balances — CEO видит остатки на upstream-аккаунтах
  // OpenAI/Anthropic/etc. в одном месте. Backend проксирует к каждому
  // провайдеру (где есть API), либо читает manual-override из БД.
  // ============================================================
  providerBalances: () =>
    runRequest(
      () =>
        client
          .get('account/admin/provider_balances')
          .json<GetProviderBalancesResponse>(),
      { url: 'account/admin/provider_balances' },
    ),
  refreshProviderBalances: () =>
    runRequest(
      () =>
        client
          .post('account/admin/provider_balances/refresh')
          .json<GetProviderBalancesResponse>(),
      { url: 'account/admin/provider_balances/refresh' },
    ),
  setManualProviderBalance: (
    provider: ProviderKey,
    body: ManualBalanceUpdate,
  ) =>
    runRequest(
      () =>
        client
          .post(
            `account/admin/provider_balances/${encodeURIComponent(provider)}/manual`,
            { json: body },
          )
          .json<ProviderBalance>(),
      { url: `account/admin/provider_balances/${provider}/manual` },
    ),

  // ============================================================
  // Provider cookies — Playwright scraper (Phase 2, 2026-05-11).
  // OpenAI/Anthropic/Together не отдают баланс через API; CEO логинится
  // в их UI, экспортирует cookies (Playwright JSON dump) и загружает их
  // через UI. Backend шифрует Fernet'ом и форвардит в brikko-scraper.
  // ============================================================
  providerCookies: () =>
    runRequest(
      () =>
        client
          .get('account/admin/provider_cookies')
          .json<GetProviderCookiesResponse>(),
      { url: 'account/admin/provider_cookies' },
    ),
  uploadProviderCookies: (provider: ScrapeProviderKey, cookiesJson: string) =>
    runRequest(
      () =>
        client
          .post(
            `account/admin/provider_cookies/${encodeURIComponent(provider)}`,
            {
              body: cookiesJson,
              headers: { 'Content-Type': 'application/json' },
            },
          )
          .json<ProviderCookieUploadResponse>(),
      { url: `account/admin/provider_cookies/${provider}` },
    ),
  deleteProviderCookies: (provider: ScrapeProviderKey) =>
    runRequest(
      () =>
        client
          .delete(
            `account/admin/provider_cookies/${encodeURIComponent(provider)}`,
          )
          .json<ProviderCookieMeta>(),
      { url: `account/admin/provider_cookies/${provider}` },
    ),
};

// Convenience wrappers used by /app/admin/balances. Они дают плоский API без
// необходимости импортировать `adminApi` целиком — полезно для будущих
// react-query хуков, которые работают с типизированными mutationFn.
export function fetchProviderBalances(): Promise<GetProviderBalancesResponse> {
  return adminApi.providerBalances();
}

export function refreshProviderBalances(): Promise<GetProviderBalancesResponse> {
  return adminApi.refreshProviderBalances();
}

export function setManualBalance(
  provider: ProviderKey,
  body: ManualBalanceUpdate,
): Promise<ProviderBalance> {
  return adminApi.setManualProviderBalance(provider, body);
}

export function fetchProviderCookies(): Promise<GetProviderCookiesResponse> {
  return adminApi.providerCookies();
}

export function uploadProviderCookies(
  provider: ScrapeProviderKey,
  cookiesJson: string,
): Promise<ProviderCookieUploadResponse> {
  return adminApi.uploadProviderCookies(provider, cookiesJson);
}

export function deleteProviderCookies(
  provider: ScrapeProviderKey,
): Promise<ProviderCookieMeta> {
  return adminApi.deleteProviderCookies(provider);
}

export { client as rawClient };
export type { Kopecks };
