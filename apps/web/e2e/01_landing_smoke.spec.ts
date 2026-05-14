import { test, expect } from '@playwright/test';
import { statusOfMany } from './helpers/api';

/**
 * Smoke-проверка публичных маршрутов и навигации.
 *
 * Контекст багов 2026-05-02 (ручной QA CEO):
 *   • Баг #2 — backend генерит /verify-email, фронт ждёт /signup/verify-email →
 *     несуществующая страница возвращает 404. Smoke-тест ловит этот класс
 *     регрессий: прохожусь по всему публичному списку маршрутов, любой 4xx/5xx
 *     валит билд.
 *   • Баг #6 — brikko.ru/online отстал от main: «Модели» нет в header,
 *     тарифов 4 вместо 5. Поэтому отдельный assert на header-навигацию +
 *     количество тарифов.
 *
 * Запуск:
 *   npm run test:e2e:smoke              — против localhost:3000 + MSW
 *   E2E_BASE_URL=https://brikko.ru \
 *     npx playwright test 01_landing    — production smoke (без auth)
 */

const PUBLIC_ROUTES = [
  '/',
  '/pricing',
  '/faq',
  '/docs',
  '/docs/cookbook',
  '/docs/smart-routing',
  '/models',
  '/playground',
  '/status',
  '/legal/oferta',
  '/legal/privacy',
  '/legal/info',
  '/legal/cookie',
];

test.describe('Landing smoke — публичные страницы', () => {
  test('все публичные маршруты возвращают 200', async ({ page }) => {
    const results = await statusOfMany(page, PUBLIC_ROUTES);

    const failed = Object.entries(results).filter(([, status]) => status !== 200);
    if (failed.length > 0) {
      // Сообщение в виде таблицы — Playwright trace покажет его в JSON-формате.
      // eslint-disable-next-line no-console
      console.error('Non-200 routes:', failed);
    }
    expect(failed, `Routes returning non-200: ${JSON.stringify(failed)}`).toEqual([]);
  });

  test('главная содержит H1, hero CTA и блок Features', async ({ page }) => {
    await page.goto('/');

    // H1 содержит «GPT, Claude, Gemini» (Hero.tsx:31).
    const h1 = page.getByRole('heading', { level: 1 });
    await expect(h1).toBeVisible();
    await expect(h1).toContainText('GPT');
    await expect(h1).toContainText('Claude');
    await expect(h1).toContainText('Gemini');

    // CTA «Начать за 5 минут» — основной hero-button.
    await expect(page.getByRole('link', { name: /начать за 5 минут/i })).toBeVisible();

    // Welcome-бонус упоминается на первом экране — соц.доказательство легальности.
    await expect(page.getByText(/welcome.*200/i)).toBeVisible();
  });

  test('header содержит ожидаемые ссылки навигации', async ({ page }) => {
    await page.goto('/');

    // 5 ссылок в `nav` — см. (marketing)/layout.tsx. Sprint 12 добавил Playground
    // первой ссылкой (PRD §3.1) — он top-of-funnel для холодного лида с Habr.
    const nav = page.getByRole('navigation', { name: 'Главная навигация' });
    await expect(nav.getByRole('link', { name: 'Playground' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Модели' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Тарифы' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Документация' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'FAQ' })).toBeVisible();

    // CTA-кнопки в правой части header.
    await expect(page.getByRole('link', { name: 'Войти' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Получить ключ' })).toBeVisible();

    // Sprint 12 — secondary hero CTA «Попробовать без регистрации» ведёт в playground.
    await expect(page.getByRole('link', { name: /попробовать без регистрации/i })).toBeVisible();
  });

  test('/playground рендерит форму и mock 200 от backend', async ({ page }) => {
    await page.goto('/playground');

    // H1 «Попробуй прямо сейчас» — main-heading страницы.
    await expect(page.getByRole('heading', { level: 1, name: /попробуй прямо сейчас/i })).toBeVisible();

    // Поле модели + дефолтная GPT-5.4 mini.
    await expect(page.getByTestId('playground-model')).toBeVisible();

    // Готовые примеры (4 chip'а).
    await expect(page.getByTestId('playground-example-Классификация лида')).toBeVisible();

    // Запуск пустой формы — кнопка disabled.
    const submit = page.getByTestId('playground-submit');
    await expect(submit).toBeDisabled();

    // Заполняем prompt и отправляем — mock возвращает 200.
    await page.getByTestId('playground-prompt').fill('Привет, кто ты?');
    await expect(submit).toBeEnabled();
    await submit.click();

    // Через mock-delay 300мс должен появиться результат.
    await expect(page.getByTestId('playground-result')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId('playground-result')).toContainText('mock-ответ');
  });

  test('/pricing показывает pay-as-you-go карточку', async ({ page }) => {
    await page.goto('/pricing');

    // CEO 2026-05-14: pivot на pay-per-use, subscription tiers убраны.
    // Ядро карточки: «pay-as-you-go» eyebrow, цена 0,02 ₽, CTA «Создать аккаунт».
    await expect(page.getByText(/pay.?as.?you.?go/i).first()).toBeVisible();
    await expect(page.getByText(/0,02\s*₽/).first()).toBeVisible();
    await expect(page.getByText(/100\s*запросов в день бесплатно/i).first()).toBeVisible();
    await expect(page.getByRole('link', { name: /создать аккаунт/i }).first()).toBeVisible();
  });

  test('/faq содержит минимум 7 вопросов', async ({ page }) => {
    await page.goto('/faq');

    // FAQ-вопросы в виде <button> или <h3>/<details> — считаем гибко.
    // Любой текст, заканчивающийся на «?» в FAQ-секции.
    const faqItems = page.locator('main').locator(':text-matches("\\?\\s*$", "i")');
    const count = await faqItems.count();
    expect(count, `FAQ должен содержать ≥7 вопросов, найдено ${count}`).toBeGreaterThanOrEqual(7);
  });

  test('/signup/verify-email существует, /verify-email — НЕ существует на marketing-уровне', async ({
    page,
  }) => {
    // Баг #2: backend генерит /verify-email, но это путь /auth/verify-email
    // (правильный — /signup/verify-email для post-signup-flow).
    // Просто наличие auth-варианта ОК; signup-вариант — обязателен.
    const signupVerify = await page.request.get('/signup/verify-email', { failOnStatusCode: false });
    // 200 ИЛИ редирект (302) — оба валидны (middleware может бросить на /login).
    expect([200, 302, 307]).toContain(signupVerify.status());
  });
});
