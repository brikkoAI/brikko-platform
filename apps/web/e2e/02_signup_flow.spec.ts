import { test, expect } from '@playwright/test';
import {
  captureSignupResponse,
  freshEmail,
  loginViaUI,
  TEST_PASSWORD,
} from './helpers/api';

/**
 * Полная воронка нового клиента: signup → verify → login → key → API call.
 *
 * Каждый assert привязан к конкретному багу из ручного QA 2026-05-02:
 *   • Баг #1 — signup response должен содержать `verify_url_dev` И фронт должен
 *     переход именно по нему (не по `?token=email`-фолбэку).
 *   • Баг #3 — /app главная не должна падать с error boundary после логина.
 *   • Баг #5 — кнопка «Готово» в модалке создания ключа должна быть кликабельна
 *     БЕЗ предварительного нажатия «Скопировать ключ».
 *   • Баг #8 — префикс выданного ключа = sk-brk-... .
 *
 * Запуск (с MSW):  npm run test:e2e:smoke
 */

test.describe('Signup flow — happy path нового клиента', () => {
  test('полный путь: signup → verify → login → создать ключ → API call', async ({ page }) => {
    const email = freshEmail('flow');

    // ────────── Шаг 1: signup ──────────
    await page.goto('/signup');
    await expect(page.getByRole('heading', { name: 'Создать аккаунт' })).toBeVisible();

    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(TEST_PASSWORD);
    await page.getByRole('checkbox').check();

    // Параллельно: клик по submit + перехват response. waitForResponse должен
    // быть зарегистрирован ДО клика — иначе race-condition.
    const [signupResp] = await Promise.all([
      captureSignupResponse(page),
      page.getByTestId('signup-submit').click(),
    ]);

    expect(signupResp.status, 'signup должен вернуть 200').toBe(200);

    // ────────── Баг #1: verify_url_dev обязателен в dev-окружении ──────────
    // Если этого поля нет в ответе — фронт не сможет показать корректную ссылку,
    // и [DEV] Перейти приведёт на 404.
    //
    // ВНИМАНИЕ: текущий MSW-mock этого поля НЕ возвращает (handlers.ts:164),
    // поэтому assert ОЖИДАЕМО упадёт до тех пор пока mock + backend не починены.
    // Это и есть цель — CI gate, который ловит регрессию воронки.
    expect(
      signupResp.body,
      'signup-response должен содержать verify_url_dev для DEV/test окружений',
    ).toHaveProperty('verify_url_dev');

    expect(typeof signupResp.body.verify_url_dev, 'verify_url_dev должен быть строкой').toBe(
      'string',
    );

    expect(
      String(signupResp.body.verify_url_dev),
      'verify_url_dev должен указывать на /signup/verify-email (не /verify-email — баг #2)',
    ).toMatch(/\/signup\/verify-email\?token=/);

    // Должны оказаться на странице ожидания подтверждения почты.
    await expect(page).toHaveURL(/\/signup\/verify-email/);
    await expect(page.getByRole('heading', { name: 'Проверь почту' })).toBeVisible();

    // ────────── Шаг 2: переход по verify-ссылке ──────────
    // Используем DEV-link тот же, что юзер видит в UI — это и есть кейс из бага #1.
    await page.getByTestId('dev-verify-link').click();

    // После verify редирект на /app + welcome-бонус 200 ₽.
    await expect(page).toHaveURL('/app', { timeout: 15_000 });

    // Баг #3: главная кабинета НЕ должна падать с error boundary.
    // Текст error-boundary в Next.js дев-сборке = "Что-то пошло не так".
    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);

    // Баланс 200 ₽ — welcome-бонус. Точный селектор зависит от компонента
    // (BalanceCard или topbar-balance). Проверяем по одному из них.
    const balanceLocators = [
      page.getByTestId('topbar-balance'),
      page.getByTestId('billing-balance'),
      page.getByText(/200\s*₽/).first(),
    ];
    let balanceVisible = false;
    for (const loc of balanceLocators) {
      if ((await loc.count()) > 0) {
        balanceVisible = true;
        break;
      }
    }
    expect(balanceVisible, 'после verify должен быть виден welcome-баланс 200 ₽').toBe(true);

    // ────────── Шаг 3: создать первый ключ ──────────
    await page.goto('/app/keys');
    await page.getByTestId('create-key-cta').click();

    // Поле name (если требуется) — fill if present.
    const nameInput = page.getByLabel(/название|name/i);
    if (await nameInput.count()) {
      await nameInput.first().fill('e2e-key');
    }

    // Submit создания ключа — кнопка часто называется "Создать".
    await page.getByRole('button', { name: /создать/i }).click();

    // Баг #8: ключ должен начинаться на sk-brk-.
    // Селектор: либо <code data-testid="new-key-value">, либо <input readonly>.
    const newKey = page.locator('[data-testid="new-key-value"], input[readonly]').first();
    await expect(newKey).toBeVisible({ timeout: 5_000 });

    const keyText =
      (await newKey.getAttribute('value')) ??
      (await newKey.textContent()) ??
      '';
    expect(keyText.trim(), 'ключ должен иметь префикс sk-brk-').toMatch(/^sk-brk-/);

    // Баг #5: кнопка «Готово» должна быть enabled БЕЗ нажатия «Скопировать».
    // Если она disabled — это UX-trap, который мы хотим запретить.
    const doneButton = page.getByRole('button', { name: /готово|закрыть/i });
    await expect(doneButton, 'кнопка «Готово» не должна быть disabled до копирования').toBeEnabled();

    // Закрываем модалку — проверяем что она реально закрывается.
    await doneButton.click();
    await expect(page.locator('[role="dialog"]')).toHaveCount(0);

    // ────────── Шаг 4: API-запрос с новым ключом ──────────
    // Прямой POST на /v1/chat/completions через page.request — у нас уже сессия
    // с MSW, ключ только что создан и должен быть валиден.
    const apiResp = await page.request.post('/v1/chat/completions', {
      headers: {
        Authorization: `Bearer ${keyText.trim()}`,
        'Content-Type': 'application/json',
      },
      data: {
        model: 'auto',
        messages: [{ role: 'user', content: 'ping' }],
      },
      failOnStatusCode: false,
    });

    expect(apiResp.status(), 'API-запрос с новым ключом должен вернуть 200').toBe(200);
    const apiBody = await apiResp.json();
    // Smart-routing: response должен содержать выбранную модель.
    expect(apiBody, 'response должен содержать поле model (smart router выбрал)').toHaveProperty(
      'model',
    );
  });

  test('signup → email уже существует → 409', async ({ page }) => {
    // Дубль-регистрация — ожидаем 409 + понятное сообщение.
    const dupEmail = freshEmail('dup');

    // Первая регистрация — успешная.
    await page.goto('/signup');
    await page.getByLabel('Email').fill(dupEmail);
    await page.getByLabel('Пароль').fill(TEST_PASSWORD);
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();
    await page.waitForURL(/\/signup\/verify-email/);

    // Вторая регистрация с тем же email.
    await page.goto('/signup');
    await page.getByLabel('Email').fill(dupEmail);
    await page.getByLabel('Пароль').fill(TEST_PASSWORD);
    await page.getByRole('checkbox').check();

    const [resp] = await Promise.all([
      captureSignupResponse(page),
      page.getByTestId('signup-submit').click(),
    ]);

    expect(resp.status).toBe(409);
    await expect(page.getByText(/уже в Brikko|уже зарегистрирован/i)).toBeVisible();
  });

  test('logout возвращает на /', async ({ page }) => {
    // Регистрируем + verify, чтобы попасть в /app.
    const email = freshEmail('logout');
    await page.goto('/signup');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(TEST_PASSWORD);
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();
    await page.waitForURL(/\/signup\/verify-email/);
    await page.getByTestId('dev-verify-link').click();
    await page.waitForURL('/app');

    // Идём в settings → logout button.
    await page.goto('/app/settings');
    await page.getByTestId('logout-button').click();

    // Должны вернуться на лендинг или /login (зависит от middleware).
    await page.waitForURL(/^https?:\/\/[^/]+\/(login)?$/, { timeout: 10_000 });
  });
});
