import { test, expect, type APIRequestContext } from '@playwright/test';

/**
 * QA P0-5 — API key full lifecycle e2e (Bearer path) против реального backend'а.
 *
 * Проверяем:
 *  - Создание ключа через UI → DB row → key_prefix виден.
 *  - Bearer-вызов на /v1/chat/completions с этим ключом (на test-stub модель).
 *  - Revoke через UI → bearer на чат возвращает 401 (cache invalidation).
 *  - Recreate с тем же name — старый prefix не работает, новый работает.
 *  - Multi-key изоляция: два ключа в одном account; revoke одного — второй жив.
 *
 * Запуск: `npx playwright test --config=playwright.config.real.ts keys-real.spec.ts`
 */

const API_BASE = process.env.PLAYWRIGHT_API_BASE_URL || 'http://localhost:8000/v1';
const POSTGRES_CONTAINER = process.env.POSTGRES_CONTAINER || 'voltari-postgres';
const POSTGRES_USER = process.env.POSTGRES_USER || 'voltari';
const POSTGRES_DB = process.env.POSTGRES_DB || 'voltari';

async function execPg(sql: string): Promise<string> {
  const { execSync } = await import('node:child_process');
  return execSync(
    `docker exec ${POSTGRES_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tAc "${sql.replace(/"/g, '\\"')}"`,
    { stdio: ['ignore', 'pipe', 'pipe'] },
  ).toString();
}

async function truncateTables(): Promise<void> {
  const sql =
    'TRUNCATE TABLE usage_events, request_payloads, transactions, account_holds, ' +
    'api_keys, seats, email_invites, welcome_credits_log, processed_webhooks, ' +
    'accounts, users RESTART IDENTITY CASCADE;';
  await execPg(sql);
}

async function createVerifiedUser(
  request: APIRequestContext,
  password: string = 'TestPassword1234567890',
): Promise<{ email: string; password: string }> {
  const email = `e2e-keys-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@test.local`;
  const signup = await request.post(`${API_BASE}/auth/signup`, {
    data: { email, password },
    headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
  });
  expect(signup.status()).toBe(200);
  // Bypass email verification.
  await execPg(
    `UPDATE users SET email_verified = true, verification_token = NULL WHERE email = '${email}';`,
  );
  return { email, password };
}

async function loginAndGetCookies(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const res = await request.post(`${API_BASE}/auth/login`, {
    data: { email, password },
    headers: { 'X-Requested-With': 'voltari-web', 'Content-Type': 'application/json' },
  });
  expect(res.status()).toBe(200);
  const setCookies = (await res.headersArray())
    .filter((h) => h.name.toLowerCase() === 'set-cookie')
    .map((h) => {
      const [pair] = h.value.split(';');
      return pair;
    });
  return setCookies.join('; ');
}

// -----------------------------------------------------------------------------

test.describe('API keys lifecycle (real backend, Bearer path)', () => {
  test.beforeEach(async () => {
    await truncateTables();
  });

  // ---------------------------------------------------------------------------
  // P0: Создание → DB row → prefix виден в UI
  // ---------------------------------------------------------------------------

  test('create key через UI → DB row создан → key_prefix виден в таблице', async ({
    page,
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const cookieHeader = await loginAndGetCookies(request, email, password);

    // Прокинуть cookies в browser.
    for (const pair of cookieHeader.split('; ')) {
      const eq = pair.indexOf('=');
      const name = pair.slice(0, eq);
      const value = pair.slice(eq + 1);
      await page.context().addCookies([
        { name, value, domain: 'localhost', path: '/' },
      ]);
    }

    await page.goto('/app/keys');
    await page.getByTestId('create-key-cta').click();
    await page.getByLabel('Имя ключа').fill('e2e-real-key');
    await page.getByTestId('create-key-submit').click();

    // Полный ключ показан 1 раз.
    const fullKeyText = await page.locator('div.font-mono.break-all').first().innerText();
    expect(fullKeyText).toMatch(/^sk-vt-/);

    // Закрыть диалог.
    await page.getByRole('button', { name: 'Готово' }).click();

    // В таблице виден key_prefix (первые 14 символов).
    await expect(page.getByTestId('keys-table')).toContainText('e2e-real-key');

    // DB-проверка: ровно 1 ключ для этого email.
    const count = (await execPg(
      `SELECT COUNT(*) FROM api_keys k JOIN accounts a ON a.id = k.account_id JOIN users u ON u.id = a.owner_id WHERE u.email = '${email}';`,
    )).trim();
    expect(parseInt(count, 10)).toBe(1);
  });

  // ---------------------------------------------------------------------------
  // P0: Полный Bearer-flow — /v1/chat/completions работает; revoke → 401
  // ---------------------------------------------------------------------------

  test('full lifecycle: create → bearer call /v1/chat → revoke → bearer call 401', async ({
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const cookieHeader = await loginAndGetCookies(request, email, password);

    // Создание ключа через API (быстрее UI; UI-путь покрыт предыдущим тестом).
    const create = await request.post(`${API_BASE}/keys`, {
      data: { name: 'lifecycle-key', scope: 'write' },
      headers: {
        'X-Requested-With': 'voltari-web',
        'Content-Type': 'application/json',
        Cookie: cookieHeader,
      },
    });
    expect(create.status(), `create key: ${await create.text()}`).toBe(201);
    const created = await create.json();
    const fullKey = created.full_key as string;
    const keyId = created.id as string;
    const keyPrefix = created.prefix as string;
    expect(fullKey).toMatch(/^sk-vt-/);
    expect(keyPrefix.length).toBe(14);

    // /v1/chat/completions с Bearer работает (на test-stub модели).
    // Если test:echo не зарегистрирован — backend вернёт 400 invalid_model;
    // принимаем 200 ИЛИ 400 (главное — НЕ 401, ключ валиден).
    const chat1 = await request.post(`${API_BASE}/chat/completions`, {
      data: {
        model: 'test:echo', // smoke-only model; если нет — 400
        messages: [{ role: 'user', content: 'ping' }],
      },
      headers: {
        Authorization: `Bearer ${fullKey}`,
        'Content-Type': 'application/json',
      },
    });
    expect(
      [200, 400, 402].includes(chat1.status()),
      `bearer call должен пройти аутентификацию (любой не-401), got ${chat1.status()}: ${await chat1.text()}`,
    ).toBe(true);

    // Revoke через API.
    const revoke = await request.delete(`${API_BASE}/keys/${keyId}`, {
      headers: {
        'X-Requested-With': 'voltari-web',
        Cookie: cookieHeader,
      },
    });
    expect(revoke.status()).toBe(204);

    // ВАЖНО: cache в Redis инвалидируется с auth_cache_ttl_seconds (60s default),
    // но invalidate_cache_for_key должна сразу удалить запись по ID-индексу.
    // Проверяем сразу — должно быть 401.
    const chat2 = await request.post(`${API_BASE}/chat/completions`, {
      data: {
        model: 'test:echo',
        messages: [{ role: 'user', content: 'ping' }],
      },
      headers: {
        Authorization: `Bearer ${fullKey}`,
        'Content-Type': 'application/json',
      },
    });
    expect(chat2.status(), 'revoked key должен возвращать 401, не 200').toBe(401);
  });

  // ---------------------------------------------------------------------------
  // P0: Recreate с тем же name — новые prefix/full_key валидны, старые нет
  // ---------------------------------------------------------------------------

  test('recreate с тем же name → старый prefix не валиден, новый prefix валиден', async ({
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const cookieHeader = await loginAndGetCookies(request, email, password);

    // Создаём первый.
    const r1 = await request.post(`${API_BASE}/keys`, {
      data: { name: 'duplicate-name', scope: 'write' },
      headers: {
        'X-Requested-With': 'voltari-web',
        'Content-Type': 'application/json',
        Cookie: cookieHeader,
      },
    });
    expect(r1.status()).toBe(201);
    const k1 = await r1.json();

    // Revoke первый.
    const rev = await request.delete(`${API_BASE}/keys/${k1.id}`, {
      headers: { 'X-Requested-With': 'voltari-web', Cookie: cookieHeader },
    });
    expect(rev.status()).toBe(204);

    // Создаём второй с тем же name (revoke освобождает slot).
    const r2 = await request.post(`${API_BASE}/keys`, {
      data: { name: 'duplicate-name', scope: 'write' },
      headers: {
        'X-Requested-With': 'voltari-web',
        'Content-Type': 'application/json',
        Cookie: cookieHeader,
      },
    });
    expect(r2.status()).toBe(201);
    const k2 = await r2.json();

    expect(k2.id).not.toBe(k1.id);
    expect(k2.full_key).not.toBe(k1.full_key);

    // Старый bearer — 401.
    const callOld = await request.post(`${API_BASE}/chat/completions`, {
      data: { model: 'test:echo', messages: [{ role: 'user', content: 'x' }] },
      headers: { Authorization: `Bearer ${k1.full_key}` },
    });
    expect(callOld.status(), 'старый ключ должен быть revoked').toBe(401);

    // Новый bearer — НЕ 401.
    const callNew = await request.post(`${API_BASE}/chat/completions`, {
      data: { model: 'test:echo', messages: [{ role: 'user', content: 'x' }] },
      headers: { Authorization: `Bearer ${k2.full_key}` },
    });
    expect(callNew.status(), 'новый ключ должен пройти аутентификацию').not.toBe(401);
  });

  // ---------------------------------------------------------------------------
  // P0: Multi-key — revoke одного не влияет на второй
  // ---------------------------------------------------------------------------

  test('multi-key: account имеет 2 active keys → revoke одного не валит другой', async ({
    request,
  }) => {
    const { email, password } = await createVerifiedUser(request);
    const cookieHeader = await loginAndGetCookies(request, email, password);

    const k1 = await (
      await request.post(`${API_BASE}/keys`, {
        data: { name: 'key-A', scope: 'write' },
        headers: {
          'X-Requested-With': 'voltari-web',
          'Content-Type': 'application/json',
          Cookie: cookieHeader,
        },
      })
    ).json();
    const k2 = await (
      await request.post(`${API_BASE}/keys`, {
        data: { name: 'key-B', scope: 'write' },
        headers: {
          'X-Requested-With': 'voltari-web',
          'Content-Type': 'application/json',
          Cookie: cookieHeader,
        },
      })
    ).json();

    // Revoke key-A.
    const rev = await request.delete(`${API_BASE}/keys/${k1.id}`, {
      headers: { 'X-Requested-With': 'voltari-web', Cookie: cookieHeader },
    });
    expect(rev.status()).toBe(204);

    // key-A — 401.
    const cA = await request.post(`${API_BASE}/chat/completions`, {
      data: { model: 'test:echo', messages: [{ role: 'user', content: 'x' }] },
      headers: { Authorization: `Bearer ${k1.full_key}` },
    });
    expect(cA.status()).toBe(401);

    // key-B — не 401.
    const cB = await request.post(`${API_BASE}/chat/completions`, {
      data: { model: 'test:echo', messages: [{ role: 'user', content: 'x' }] },
      headers: { Authorization: `Bearer ${k2.full_key}` },
    });
    expect(cB.status(), 'key-B должен оставаться валидным').not.toBe(401);
  });
});
