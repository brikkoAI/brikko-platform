import type { Page, Response } from '@playwright/test';

/**
 * E2E helpers — минимальный набор обёрток вокруг page.request / network интерсепта.
 *
 * Контекст: 2026-05-02 ручной QA нашёл 8 багов воронки нового клиента, которые
 * не ловятся unit-тестами. Этот файл — общий слой для тестов 01_landing_smoke,
 * 02_signup_flow, 03_dashboard.
 *
 * Принципы:
 *  - НЕ дублируем MSW-фикстуры — тесты бьются о тот же mock, что и в `signup.spec.ts`.
 *  - Где нужна проверка контракта (баг #1: фронт не читает verify_url_dev) —
 *    интерсептим response через page.waitForResponse.
 *  - Никакой авторизации в helper'ах: вход — через UI (это и тестируем).
 */

export const E2E_BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

/**
 * Уникальный email на каждый прогон — иначе MSW state.users сохраняет дубль
 * и тест падает на 409 email_already_registered.
 */
export function freshEmail(prefix = 'e2e'): string {
  // ms + random — на случай parallel-прогона в CI (в нашем случае workers=1, но всё же).
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}@example.com`;
}

/**
 * Стандартный пароль для тестовых юзеров. ≥10 символов (требование MSW handlers.ts:119).
 */
export const TEST_PASSWORD = 'verysecure-password-1';

/**
 * Ждёт ответ POST /auth/signup и возвращает распарсенный JSON.
 *
 * Используется в `02_signup_flow.spec.ts` для проверки контракта signup.
 * **Баг #1 (2026-05-02):** backend генерит поле `verify_url_dev`, но фронт его
 * не использует — тест assert'ит наличие поля в ответе. Если backend перестанет
 * присылать — тест упадёт раньше юзера.
 */
export async function captureSignupResponse(page: Page): Promise<{
  status: number;
  body: Record<string, unknown>;
}> {
  const response = await page.waitForResponse(
    (resp) => /\/v1\/auth\/signup\b/.test(resp.url()) && resp.request().method() === 'POST',
    { timeout: 10_000 },
  );
  const body = (await response.json()) as Record<string, unknown>;
  return { status: response.status(), body };
}

/**
 * GET всех URL из списка через page.request, возвращает map { url → status }.
 *
 * Использует тот же storage state что и страница (cookies/headers), чтобы
 * MSW-перехват тоже сработал. Для статических страниц не критично, но даёт
 * единообразное поведение между моком и реальным backend.
 */
export async function statusOfMany(
  page: Page,
  paths: string[],
): Promise<Record<string, number>> {
  const results: Record<string, number> = {};
  // Запросы по одному — параллель имеет минимальный выигрыш при 12 страницах
  // и усложняет логи. К тому же MSW worker — single-thread.
  for (const path of paths) {
    const url = path.startsWith('http') ? path : `${E2E_BASE_URL}${path}`;
    let resp: Response | null = null;
    try {
      resp = await page.request.get(url, { failOnStatusCode: false });
      results[path] = resp.status();
    } catch (err) {
      // Network error → -1, чтобы тест явно падал без exception в трассе.
      results[path] = -1;
    }
  }
  return results;
}

/**
 * Logs in via /login UI — используется в 02_signup_flow и 03_dashboard.
 * Ждёт редиректа на /app, иначе бросает.
 */
export async function loginViaUI(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Пароль').fill(password);
  await page.getByRole('button', { name: /войти/i }).click();
  await page.waitForURL(/\/app(\/|$)/, { timeout: 15_000 });
}
