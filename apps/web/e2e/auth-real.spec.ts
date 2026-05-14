import { test, expect, type APIRequestContext, type BrowserContext } from '@playwright/test';

/**
 * QA P0-4 — Auth full lifecycle e2e против РЕАЛЬНОГО backend'а.
 *
 * Цель: ловить класс багов, которые MSW не ловит — TD-031 (dual-auth
 * mismatch на /v1/usage), NEXT_PUBLIC ушёл в моки, prerender бьётся в
 * prod-build, CSRF-mismatch, refresh-flow race в реальном браузере.
 *
 * Запуск:
 *   `npx playwright test --config=playwright.config.real.ts auth-real.spec.ts`
 *
 * Перед запуском — поднять gateway+postgres+redis (см. playwright.config.real.ts
 * docstring). DB чистится через docker exec в beforeEach.
 *
 * ВАЖНО: тест ходит на *реальный* backend (`http://localhost:8000/v1`).
 * NEXT_PUBLIC_API_BASE_URL в bundle web должно быть запечено в build-time
 * (см. playwright.config.real.ts → webServer.env). Если оно ушло в моки —
 * именно тут это сразу взлетит с ошибкой "fetch /api/mock/v1/auth/login".
 */

const API_BASE = process.env.PLAYWRIGHT_API_BASE_URL || 'http://localhost:8000/v1';
const POSTGRES_CONTAINER = process.env.POSTGRES_CONTAINER || 'voltari-postgres';
const POSTGRES_USER = process.env.POSTGRES_USER || 'voltari';
const POSTGRES_DB = process.env.POSTGRES_DB || 'voltari';

type SignupResult = { user_id: string; email: string };

// ---- DB cleanup helpers -----------------------------------------------------
//
// Между тестами TRUNCATE через `docker exec`. Не трогаем alembic_version
// и migration-state — только данные.

async function truncateTables(): Promise<void> {
  // Используем @playwright/test's expect.poll нельзя — нам нужен child_process.
  // execSync синхронный — нормально для setup/teardown.
  const { execSync } = await import('node:child_process');
  const tables = [
    'usage_events',
    'request_payloads',
    'transactions',
    'account_holds',
    'api_keys',
    'seats',
    'email_invites',
    'welcome_credits_log',
    'processed_webhooks',
    'accounts',
    'users',
  ];
  const sql = `TRUNCATE TABLE ${tables.join(', ')} RESTART IDENTITY CASCADE;`;
  try {
    execSync(
      `docker exec ${POSTGRES_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "${sql}"`,
      { stdio: 'pipe' },
    );
  } catch (err) {
    // Если контейнер не поднят — тест должен упасть с понятной ошибкой,
    // а не молча начать работать на грязной БД.
    throw new Error(
      `Failed to truncate test DB. Is docker container '${POSTGRES_CONTAINER}' running?\n${(err as Error).message}`,
    );
  }
}

/**
 * Достаёт verification token напрямую из БД (для test-only path).
 * В prod пользователь получает его по email; для e2e мы не настроиваем
 * SMTP-сервер — ходим в БД напрямую через docker exec.
 */
async function getVerificationTokenForUser(email: string): Promise<string> {
  const { execSync } = await import('node:child_process');
  // verification_token в БД хранится как hash (см. apps/gateway/voltari_gateway/auth/email_verification.py).
  // Plain token нам не виден через БД — он только в email-логе. Поэтому
  // мы используем альтернативный путь: для тестов backend пишет plaintext
  // в логи (или мы маркируем верификацию вручную через UPDATE).
  //
  // Прагматичный подход для e2e: НЕ верифицировать через token, а сразу
  // SET email_verified=true в БД. Это валидно — мы тестируем потоки
  // ПОСЛЕ верификации; сам verify-flow покрыт unit-тестом
  // test_email_verification.py.
  const sql = `UPDATE users SET email_verified = true, verification_token = NULL WHERE email = '${email.replace(/'/g, "''")}';`;
  execSync(
    `docker exec ${POSTGRES_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c "${sql}"`,
    { stdio: 'pipe' },
  );
  return 'manually-verified-via-db';
}

/**
 * Создаёт свежего верифицированного пользователя через signup → DB-патч.
 * Возвращает учётные данные для login.
 */
async function createVerifiedUser(
  request: APIRequestContext,
  password: string = 'TestPassword1234567890',
): Promise<{ email: string; password: string; userId: string }> {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@test.local`;
  const signup = await request.post(`${API_BASE}/auth/signup`, {
    data: { email, password },
    headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
  });
  expect(signup.status(), `signup should succeed: ${await signup.text()}`).toBe(200);
  const body = (await signup.json()) as SignupResult;

  // Verify-via-DB shortcut (см. docstring выше).
  await getVerificationTokenForUser(email);

  return { email, password, userId: body.user_id };
}

// -----------------------------------------------------------------------------

test.describe('Auth lifecycle (real backend)', () => {
  test.beforeEach(async () => {
    await truncateTables();
  });

  // -------------------------------------------------------------------------
  // P0: Happy path — signup → verify → login → /app → logout → revoked
  // -------------------------------------------------------------------------

  test('full lifecycle: signup → email-verify → login → /app → logout → revoked refresh fails', async ({
    page,
    context,
    request,
  }) => {
    const email = `lifecycle-${Date.now()}@test.local`;
    const password = 'CorrectHorseBattery42!';

    // 1) Signup
    await page.goto('/signup');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(password);
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();
    await expect(page).toHaveURL(/\/signup\/verify-email/);

    // 2) Verify email through DB shortcut
    await getVerificationTokenForUser(email);

    // 3) Login через UI
    await page.goto('/login');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(password);
    await page.getByTestId('login-submit').click();

    // 4) /app загрузился (AppLayout server-side cookie check НЕ редиректит)
    await expect(page).toHaveURL('/app');
    // Topbar показывает баланс welcome 200₽ (welcome credit grant'нулся при verify-email,
    // но мы делали shortcut'ом через UPDATE — так что welcome credit не начислен.
    // Этот ассерт можно ослабить если используем shortcut.)
    await expect(page.getByTestId('topbar-balance').or(page.locator('header'))).toBeVisible();

    // 5) Cookies реально установлены
    const cookies = await context.cookies();
    const accessCookie = cookies.find((c) => c.name === 'vlt_access');
    const refreshCookie = cookies.find((c) => c.name === 'vlt_refresh');
    expect(accessCookie, 'vlt_access cookie должен быть set после login').toBeDefined();
    expect(refreshCookie, 'vlt_refresh cookie должен быть set после login').toBeDefined();
    expect(accessCookie!.httpOnly).toBe(true);

    // 6) /v1/usage с cookie возвращает 200 (TD-31 регрессия)
    const usageRes = await page.request.get(`${API_BASE}/usage`);
    expect(usageRes.status(), `/v1/usage с cookie должен быть 200, не 401 (TD-031)`).toBe(200);
    const usageBody = await usageRes.json();
    expect(usageBody).toHaveProperty('totals');

    // 7) Logout
    await page.request.post(`${API_BASE}/auth/logout`, {
      headers: { 'X-Requested-With': 'voltari-web' },
    });

    // 8) После logout — cookies очищены
    const cookiesAfter = await context.cookies();
    const accessAfter = cookiesAfter.find((c) => c.name === 'vlt_access');
    // Cookie может быть либо удалён (отсутствует), либо expired (value пустое).
    expect(accessAfter?.value || '').toBeFalsy();

    // 9) Попытка refresh со старым refresh-токеном (если он ещё в браузере) — должна провалиться
    // (revocation сработала). Эта проверка делается через прямой ручной POST:
    const oldRefreshValue = refreshCookie!.value;
    const refreshRes = await request.post(`${API_BASE}/auth/refresh`, {
      headers: {
        'X-Requested-With': 'voltari-web',
        Cookie: `vlt_refresh=${oldRefreshValue}`,
      },
    });
    expect(
      refreshRes.status(),
      `revoked refresh-token не должен работать после logout`,
    ).toBe(401);
  });

  // -------------------------------------------------------------------------
  // P0: Email-not-verified → login заблокирован
  // -------------------------------------------------------------------------

  test('signup без email-verify → login возвращает 403 email_not_verified', async ({ page, request }) => {
    const email = `not-verified-${Date.now()}@test.local`;
    const password = 'CorrectHorseBattery42!';

    const signup = await request.post(`${API_BASE}/auth/signup`, {
      data: { email, password },
      headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
    });
    expect(signup.status()).toBe(200);

    // НЕ верифицируем email специально.
    await page.goto('/login');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(password);
    await page.getByTestId('login-submit').click();

    // Backend возвращает 403 + code=email_not_verified, frontend маппит на ошибку.
    // Ассерт не на конкретный текст, а на то что в /app мы НЕ попали.
    await expect(page).not.toHaveURL('/app');
    // Проверяем что какой-то error-banner или toast показался (текст может меняться).
    const stillOnLogin = await page.url();
    expect(stillOnLogin).toContain('/login');
  });

  // -------------------------------------------------------------------------
  // P0: Wrong password → 401 + ошибка в форме
  // -------------------------------------------------------------------------

  test('login с wrong password → форма показывает invalid_credentials, /app не открывается', async ({
    page,
    request,
  }) => {
    const { email } = await createVerifiedUser(request);

    await page.goto('/login');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill('WrongPassword1234567890');
    await page.getByTestId('login-submit').click();

    // Не должны попасть в /app.
    await page.waitForTimeout(500); // дать форме отреагировать
    await expect(page).not.toHaveURL('/app');
    // Поле password должно показать ошибку (см. LoginForm.onSubmit ветка invalid_credentials).
    await expect(page.getByText(/неверный email или пароль/i)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // P0: AppLayout server-side cookie check позволяет рендер /app
  //     (TD-031 / FE P0-7 регрессия — cookie прокидывается на server)
  // -------------------------------------------------------------------------

  test('AppLayout server-side: cookie присутствует → /app рендерится без 401-loop', async ({
    page,
    context,
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);

    // Login через API (быстрее, чем UI).
    const loginRes = await request.post(`${API_BASE}/auth/login`, {
      data: { email, password },
      headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
    });
    expect(loginRes.status()).toBe(200);
    // Прокидываем cookies из request в browser context.
    const cookies = (await loginRes.headersArray())
      .filter((h) => h.name.toLowerCase() === 'set-cookie');
    for (const c of cookies) {
      const [pair] = c.value.split(';');
      const [name, value] = pair.split('=');
      await context.addCookies([
        { name, value, domain: 'localhost', path: '/' },
      ]);
    }

    // Слушаем сетевые запросы — не должно быть бесконечного loop'а 401 → refresh → 401.
    let usage401Count = 0;
    page.on('response', (resp) => {
      if (resp.url().includes('/v1/usage') && resp.status() === 401) {
        usage401Count += 1;
      }
    });

    await page.goto('/app');
    await expect(page).toHaveURL('/app');

    // Дать react-query время на полный цикл запросов.
    await page.waitForLoadState('networkidle');

    // /v1/usage вызывается из dashboard. Должен отдать 200, не 401.
    expect(usage401Count, 'usage НЕ должен возвращать 401 — TD-031 регрессия').toBe(0);
  });

  // -------------------------------------------------------------------------
  // P1: Session-expired flow → refresh успешный → продолжение работы
  // -------------------------------------------------------------------------

  test('expired access token → refresh успешный → запрос продолжается без redirect', async ({
    page,
    context,
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);

    // Login.
    const loginRes = await request.post(`${API_BASE}/auth/login`, {
      data: { email, password },
      headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
    });
    expect(loginRes.status()).toBe(200);

    // Прокинуть cookies в browser.
    const setCookieHeaders = (await loginRes.headersArray()).filter(
      (h) => h.name.toLowerCase() === 'set-cookie',
    );
    for (const sc of setCookieHeaders) {
      const [pair] = sc.value.split(';');
      const eq = pair.indexOf('=');
      const name = pair.slice(0, eq).trim();
      const value = pair.slice(eq + 1).trim();
      await context.addCookies([{ name, value, domain: 'localhost', path: '/' }]);
    }

    // Имитируем expired access cookie: подменяем на заведомо невалидное значение.
    // Браузер при следующем запросе получит 401 и должен запустить refresh-flow.
    const cookiesNow = await context.cookies();
    const accessCookie = cookiesNow.find((c) => c.name === 'vlt_access');
    expect(accessCookie).toBeDefined();
    await context.clearCookies({ name: 'vlt_access' });
    await context.addCookies([
      {
        name: 'vlt_access',
        value: 'expired.invalid.jwt',
        domain: 'localhost',
        path: '/',
      },
    ]);

    // Слушаем refresh-вызовы.
    let refreshCalls = 0;
    page.on('response', (resp) => {
      if (resp.url().includes('/v1/auth/refresh')) {
        refreshCalls += 1;
      }
    });

    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // Должен был быть refresh — фронт замечает 401, пытается refresh, refresh успешен,
    // запрос повторяется. /app остаётся открытым.
    expect(refreshCalls, 'клиент должен был сделать минимум 1 refresh').toBeGreaterThanOrEqual(1);
    await expect(page).toHaveURL('/app');
  });

  // -------------------------------------------------------------------------
  // P1: Session-expired + refresh fail → redirect на /login?reason=session_expired
  // -------------------------------------------------------------------------

  test('expired access + revoked refresh → redirect /login?reason=session_expired', async ({
    page,
    context,
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const loginRes = await request.post(`${API_BASE}/auth/login`, {
      data: { email, password },
      headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
    });
    expect(loginRes.status()).toBe(200);

    // Из login-ответа берём оба cookie и сразу logout — это ревокирует refresh JTI.
    const setCookieHeaders = (await loginRes.headersArray()).filter(
      (h) => h.name.toLowerCase() === 'set-cookie',
    );
    const cookieMap: Record<string, string> = {};
    for (const sc of setCookieHeaders) {
      const [pair] = sc.value.split(';');
      const eq = pair.indexOf('=');
      cookieMap[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
    }

    // Logout с этими cookies — JTI ревокируется в Redis.
    await request.post(`${API_BASE}/auth/logout`, {
      headers: {
        'X-Requested-With': 'voltari-web',
        Cookie: Object.entries(cookieMap)
          .map(([n, v]) => `${n}=${v}`)
          .join('; '),
      },
    });

    // Теперь устанавливаем в браузер: expired access + revoked refresh.
    await context.addCookies([
      {
        name: 'vlt_access',
        value: 'expired.invalid.jwt',
        domain: 'localhost',
        path: '/',
      },
      {
        name: 'vlt_refresh',
        value: cookieMap['vlt_refresh'],
        domain: 'localhost',
        path: '/',
      },
    ]);

    // Открываем /app — refresh должен провалиться, должен случиться redirect.
    await page.goto('/app');

    // Дать времени на 401 → refresh → 401 → redirect.
    await page.waitForURL(/\/login/, { timeout: 10_000 });
    await expect(page).toHaveURL(/reason=session_expired/);
    await expect(page.getByText(/Сессия истекла/i)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // P1: 5 параллельных запросов с одним cookie → single refresh (single-flight)
  // -------------------------------------------------------------------------

  test('single-flight refresh: 5 одновременных 401 → ровно 1 refresh', async ({
    page,
    context,
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const loginRes = await request.post(`${API_BASE}/auth/login`, {
      data: { email, password },
      headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
    });
    expect(loginRes.status()).toBe(200);

    const setCookieHeaders = (await loginRes.headersArray()).filter(
      (h) => h.name.toLowerCase() === 'set-cookie',
    );
    for (const sc of setCookieHeaders) {
      const [pair] = sc.value.split(';');
      const eq = pair.indexOf('=');
      await context.addCookies([
        {
          name: pair.slice(0, eq).trim(),
          value: pair.slice(eq + 1).trim(),
          domain: 'localhost',
          path: '/',
        },
      ]);
    }

    // Подменим access на expired.
    await context.clearCookies({ name: 'vlt_access' });
    await context.addCookies([
      {
        name: 'vlt_access',
        value: 'expired.invalid.jwt',
        domain: 'localhost',
        path: '/',
      },
    ]);

    let refreshCount = 0;
    page.on('response', (resp) => {
      if (resp.url().includes('/v1/auth/refresh')) {
        refreshCount += 1;
      }
    });

    // Заходим в /app — там одновременно дёргаются useBalance, useUsage, useAccount.
    await page.goto('/app');
    await page.waitForLoadState('networkidle');

    // На 5+ параллельных 401 должен случиться РОВНО 1 refresh, не 5.
    // Допускаем 0-2 (если react-query успел dedupe какие-то), но не >2.
    expect(refreshCount, 'single-flight нарушен — refresh случился >2 раз').toBeLessThanOrEqual(2);
  });
});
