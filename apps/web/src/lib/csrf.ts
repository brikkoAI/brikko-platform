/**
 * CSRF token management — клиентская половина double-submit pattern.
 *
 * **СТАТУС:** ✅ Final implementation (FE P0-5 — Sprint 2). Контракт описан
 * в `docs/csrf_protocol.md`.
 *
 * Поток токена:
 *   1. **Bootstrap.** При первом mutating-запросе (или после `clearCsrfToken`)
 *      делаем `GET /v1/auth/csrf`. Backend ставит cookie `vlt_csrf` и
 *      возвращает `{ csrf_token }` в JSON body. Кэшируем.
 *   2. **Mutating request.** Шлём `X-CSRF-Token: <cached>` (cookie браузер
 *      кладёт сам). Backend сравнивает header == cookie через
 *      constant-time compare. Mismatch → `403 csrf_invalid`.
 *   3. **Login / Refresh.** Backend возвращает свежий `csrf_token` в body —
 *      зовём `setCsrfTokenFromResponse`. Старый token больше не валиден.
 *   4. **Logout.** `clearCsrfToken()` сбрасывает кэш; cookie очищается
 *      backend'ом через `Set-Cookie: vlt_csrf=; Max-Age=0`.
 *   5. **403 csrf_invalid.** `lib/api.ts::runRequest` зовёт
 *      `clearCsrfToken()` и делает один retry — следующий запрос триггернёт
 *      bootstrap.
 *
 * Backwards compat (Sprint 2 → удаляется в Sprint 3 / TD-036):
 *   Backend пока ещё принимает legacy `X-Requested-With: voltari-web`,
 *   если ни header `X-CSRF-Token`, ни cookie `vlt_csrf` не присутствуют.
 *   Мы шлём оба header'а одновременно — это совместимо с любой стороной
 *   миграции и не ломается даже если backend временно откатится.
 *
 * Безопасность кэша:
 *   - Хранится **в module-level переменной** (НЕ в localStorage / sessionStorage):
 *     XSS не может прочитать через `localStorage.getItem`.
 *   - Single-flight через `inflightFetch`: 5 параллельных mutating-запросов
 *     при пустом кэше → один сетевой fetch, остальные ждут результат.
 *
 * Связанные tech debt:
 *   - TD-036 (удалить legacy `X-Requested-With` после полного rollout этого модуля)
 */

import type { LoginResponse } from './types';

/** Header name по контракту backend'а (см. csrf_protocol.md §3). */
export const CSRF_HEADER_NAME = 'X-CSRF-Token';

/**
 * Legacy header — backend пока принимает `X-Requested-With: voltari-web`
 * как fallback. Шлём ОБА header'а во время миграции (Sprint 2). После
 * Sprint 3 (TD-036) — удалить.
 */
export const LEGACY_CSRF_HEADER_NAME = 'X-Requested-With';
export const LEGACY_CSRF_VALUE = 'voltari-web';

/** Endpoint bootstrap'а CSRF-токена (см. csrf_protocol.md §1). */
const CSRF_BOOTSTRAP_PATH = 'auth/csrf';

let cachedToken: string | null = null;
let inflightFetch: Promise<string> | null = null;

interface CsrfBootstrapResponse {
  csrf_token: string;
}

/**
 * Резолв `apiBaseUrl`. Импортируем lazy чтобы избежать циклической
 * зависимости с `lib/api.ts` (который сам импортирует csrf для beforeRequest hook).
 *
 * Возвращает абсолютный base URL без trailing slash, например
 * `http://localhost:8000/v1`. NEXT_PUBLIC_API_BASE_URL запекается на build time.
 */
function resolveApiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!env || /localhost:3000/.test(env)) {
    // MSW-режим: токен делает GET на /api/mock/v1/auth/csrf — handlers
    // должны его поддерживать. См. mocks/handlers.ts.
    return '/api/mock/v1';
  }
  return env.replace(/\/$/, '');
}

/**
 * Достаёт свежий CSRF token из backend.
 *
 * Контракт: `GET /v1/auth/csrf` (public, no auth) → `{ csrf_token: string }`
 * + `Set-Cookie: vlt_csrf=...; SameSite=Strict`. Браузер сохраняет cookie
 * автоматически через `credentials: 'include'`.
 *
 * Не использует `ky` — мы хотим избежать циркулярной зависимости и
 * избежать любых hooks (например, refresh-flow на 401), которые могли бы
 * рекурсивно вызвать снова csrf bootstrap.
 */
async function fetchCsrfToken(): Promise<string> {
  const base = resolveApiBase();
  const res = await fetch(`${base}/${CSRF_BOOTSTRAP_PATH}`, {
    method: 'GET',
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`CSRF bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as CsrfBootstrapResponse;
  if (typeof body.csrf_token !== 'string' || body.csrf_token.length === 0) {
    throw new Error('CSRF bootstrap response missing `csrf_token`');
  }
  return body.csrf_token;
}

/**
 * Возвращает CSRF token (cached → fetch → cache). Single-flight.
 *
 * **Поведение при ошибке fetch:** возвращаем `LEGACY_CSRF_VALUE` как
 * graceful degradation. Backend (Sprint 2) всё ещё принимает legacy header
 * как fallback, поэтому mutating-запрос пройдёт. В Sprint 3, когда legacy
 * убран, эта ветка станет hard error — нужно будет либо retry, либо показать
 * пользователю баннер «нет связи с сервером».
 */
export async function getCsrfToken(): Promise<string> {
  if (cachedToken !== null) return cachedToken;
  if (inflightFetch !== null) return inflightFetch;

  inflightFetch = (async () => {
    try {
      const token = await fetchCsrfToken();
      cachedToken = token;
      return token;
    } catch {
      // Graceful fallback на legacy. После TD-036 (Sprint 3) — заменить на throw.
      cachedToken = LEGACY_CSRF_VALUE;
      return LEGACY_CSRF_VALUE;
    }
  })().finally(() => {
    inflightFetch = null;
  });

  return inflightFetch;
}

/**
 * Прямо записывает токен в кэш — вызывается из `useLogin`/`useSignup` /
 * `attemptRefresh` когда backend возвращает свежий token в response body.
 *
 * Без этого после login пришлось бы делать ещё один `GET /v1/auth/csrf`
 * (рост latency и лишний round-trip). Backend уже создал новый token и
 * передал его через `LoginResponse.csrf_token` — используем сразу.
 */
export function setCsrfTokenFromResponse(response: Pick<LoginResponse, 'csrf_token'>): void {
  if (response.csrf_token && response.csrf_token.length > 0) {
    cachedToken = response.csrf_token;
    inflightFetch = null;
  }
}

/**
 * Сбрасывает кэш — вызывается на logout / 403 csrf_invalid.
 * Следующий getCsrfToken() сделает новый bootstrap.
 */
export function clearCsrfToken(): void {
  cachedToken = null;
  inflightFetch = null;
}

/**
 * Только для тестов: инжектируем токен в кэш напрямую без сетевого запроса.
 * НЕ использовать в production-коде. Помечено `__` префиксом.
 */
export function __setCsrfTokenForTest(token: string | null): void {
  cachedToken = token;
}
