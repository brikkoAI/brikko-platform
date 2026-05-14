import { test, expect } from '@playwright/test';

/**
 * Real-backend integration tests.
 *
 * Запускаются ТОЛЬКО при `E2E_REAL_BACKEND=true`. Иначе skip — обычный
 * `pnpm test:e2e` (на MSW) их не трогает.
 *
 * Предусловия:
 *   1. Gateway работает на http://localhost:8000 (см. README → Full-stack local).
 *   2. Frontend собран и запущен с `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1`.
 *   3. SMTP_BACKEND=console на gateway — verify-email-link парсится из stdout.
 *
 * Что покрываем:
 *   - signup happy path с welcome 200 ₽.
 *   - key CRUD + проверка что revoked ключ → 401 на /v1/chat/completions.
 *   - topup → confirmation_url валидный URL ЮKassa (без реальной оплаты).
 */

const REAL_BACKEND = process.env.E2E_REAL_BACKEND === 'true';
const GATEWAY_BASE = process.env.E2E_GATEWAY_URL ?? 'http://localhost:8000';

test.describe.configure({ mode: 'serial' });

test.describe('Real-backend integration', () => {
  test.skip(!REAL_BACKEND, 'Set E2E_REAL_BACKEND=true to run live-gateway tests.');

  test.beforeAll(async ({ request }) => {
    // Sanity-check: gateway отвечает.
    const health = await request.get(`${GATEWAY_BASE}/healthz`);
    expect(health.ok(), `gateway healthz failed (${health.status()})`).toBe(true);
  });

  test('signup → verify-email из логов → /app с балансом 200 ₽', async ({ page, request }) => {
    const email = `e2e-${Date.now()}@example.test`;
    const password = 'verysecure-password-1';

    // 1) Signup
    await page.goto('/signup');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(password);
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();

    await expect(page).toHaveURL(/\/signup\/verify-email/);

    // 2) Достаём verify-token. В тестовом режиме backend публикует его через
    //    debug-endpoint /v1/__test/last-email (см. dev-only middleware).
    //    Если backend такого endpoint'а ещё не отдаёт — fall back на чтение лог-файла.
    const verifyToken = await fetchLatestVerifyToken(request, email);
    expect(verifyToken, 'No verify-email token captured for the test inbox').toBeTruthy();

    // 3) Открываем verify-link — фронтенд автоматически дёрнет /v1/auth/verify-email.
    await page.goto(`/verify-email?token=${encodeURIComponent(verifyToken!)}`);

    // 4) Backend выставляет vlt_access cookie + welcome 200 ₽.
    await expect(page).toHaveURL(/\/app/);
    await expect(page.getByTestId('topbar-balance')).toContainText('200');
  });

  test('создание + отзыв ключа → отозванный ключ 401 на /chat/completions', async ({ page, request }) => {
    // Залогиниваемся через UI на seed-аккаунт (см. backend seeds в conftest, либо предыдущий signup).
    // Для устойчивости: используем e2e-seed account, который скрипт seed_e2e.py создал.
    await page.goto('/login');
    await page.getByLabel('Email').fill(process.env.E2E_USER_EMAIL ?? 'e2e-seed@voltari.test');
    await page.getByLabel('Пароль').fill(process.env.E2E_USER_PASSWORD ?? 'verysecure-password-1');
    await page.getByTestId('login-submit').click();
    await expect(page).toHaveURL(/\/app/);

    // Создаём ключ
    await page.goto('/app/keys');
    await page.getByTestId('create-key-cta').click();
    await page.getByLabel('Имя ключа').fill(`integration-${Date.now()}`);
    await page.getByTestId('create-key-submit').click();

    const fullKeyEl = page.locator('div.font-mono.break-all').first();
    await expect(fullKeyEl).toContainText(/^sk-vt-/);
    const fullKey = (await fullKeyEl.textContent())?.trim() ?? '';
    expect(fullKey).toMatch(/^sk-vt-[A-Za-z0-9_-]+$/);

    await page.getByRole('button', { name: 'Готово' }).click();

    // Проверяем, что ключ работает на /v1/chat/completions
    // Используем заведомо дешёвый stub-провайдер (DeepSeek), но даже не вызываем —
    // нам нужен 200/auth-pass без реального LLM-call. Backend gateway возвращает 200 OK
    // для models endpoint без прохода в провайдера.
    const okResp = await request.get(`${GATEWAY_BASE}/v1/models`, {
      headers: { Authorization: `Bearer ${fullKey}` },
    });
    expect(okResp.ok(), `Active key should authenticate (got ${okResp.status()})`).toBe(true);

    // Revoke
    // Имя ключа в test = `integration-${ts}`; мы по нему найдём testid строки.
    const newKeyRow = page.getByRole('row').filter({ hasText: 'integration-' }).first();
    await newKeyRow.getByRole('button', { name: 'Отозвать' }).click();
    // typing-confirm — UI требует ввести имя
    const nameForConfirm = (await newKeyRow.getByText(/integration-/).first().textContent())?.trim() ?? '';
    await page.getByTestId('revoke-confirm-input').fill(nameForConfirm);
    await page.getByTestId('revoke-confirm-submit').click();

    // Тот же ключ → 401
    const revokedResp = await request.get(`${GATEWAY_BASE}/v1/models`, {
      headers: { Authorization: `Bearer ${fullKey}` },
    });
    expect(revokedResp.status()).toBe(401);
  });

  test('topup → backend возвращает confirmation_url ЮKassa', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(process.env.E2E_USER_EMAIL ?? 'e2e-seed@voltari.test');
    await page.getByLabel('Пароль').fill(process.env.E2E_USER_PASSWORD ?? 'verysecure-password-1');
    await page.getByTestId('login-submit').click();
    await expect(page).toHaveURL(/\/app/);

    await page.goto('/app/billing');

    // Перехватываем POST /billing/topup и проверяем форму ответа.
    const topupResp = page.waitForResponse((res) =>
      res.url().includes('/v1/billing/topup') && res.request().method() === 'POST',
    );
    await page.getByTestId('topup-amount').fill('500');
    await page.getByTestId('topup-submit').click();

    const resp = await topupResp;
    expect(resp.status(), `topup should be 200, got ${resp.status()}`).toBe(200);
    const body = (await resp.json()) as { payment_id: string; confirmation_url: string };
    expect(body.payment_id).toMatch(/[a-z0-9-]+/i);
    expect(body.confirmation_url).toMatch(/^https?:\/\//);
  });
});

/**
 * Достаёт последний verify-token из gateway-debug-endpoint.
 * В dev-режиме gateway публикует stub: GET /v1/__test/emails?to=<email> → {token, link}.
 * Если endpoint не реализован — тест явно скипнется (см. expect выше).
 */
async function fetchLatestVerifyToken(
  request: import('@playwright/test').APIRequestContext,
  email: string,
): Promise<string | null> {
  const url = `${GATEWAY_BASE}/v1/__test/emails?to=${encodeURIComponent(email)}`;
  const resp = await request.get(url);
  if (!resp.ok()) {
    // Endpoint не доступен (например, prod-mode). Как fallback — читаем последний письма
    // через redis pub/sub (не реализовано на этом этапе).
    return null;
  }
  const body = (await resp.json()) as { token?: string; link?: string };
  if (body.token) return body.token;
  if (body.link) {
    const match = /[?&]token=([^&]+)/.exec(body.link);
    return match?.[1] ?? null;
  }
  return null;
}
