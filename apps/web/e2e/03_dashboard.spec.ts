import { test, expect } from '@playwright/test';
import { freshEmail, TEST_PASSWORD } from './helpers/api';

/**
 * Регрессия по разделам кабинета — все ключевые страницы /app/* должны
 * рендериться без error boundary и без «Не удалось загрузить настройки»-баннеров.
 *
 * Контекст багов 2026-05-02 (ручной QA CEO):
 *   • Баг #3 — /app главная падает с error boundary («Что-то пошло не так»).
 *   • Баг #4 — /app/billing → блок «Авто-пополнение» падает с
 *     «Не удалось загрузить настройки» (useAutorefill API ломан).
 *
 * Стратегия: один beforeEach создаёт юзера + verify, остальные тесты пользуются
 * этой сессией. Цена прогона — один signup-цикл.
 */

test.describe('Dashboard regression — разделы /app', () => {
  test.beforeEach(async ({ page }) => {
    // Inline signup → verify → попали в /app.
    const email = freshEmail('dash');
    await page.goto('/signup');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill(TEST_PASSWORD);
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();
    await page.waitForURL(/\/signup\/verify-email/);
    await page.getByTestId('dev-verify-link').click();
    await page.waitForURL('/app', { timeout: 15_000 });
  });

  test('/app главная — без error boundary (баг #3)', async ({ page }) => {
    // Уже на /app благодаря beforeEach. Просто проверяем что страница цела.
    await expect(page).toHaveURL('/app');

    // Нет глобального error-boundary текста.
    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
    await expect(page.getByText(/попробуйте обновить страницу/i)).toHaveCount(0);

    // Виден один из welcome/dashboard-баннеров (любая семантика — но что-то
    // должно быть).
    const dashboardSignals = [
      page.getByTestId('welcome-banner'),
      page.getByTestId('zero-balance-banner'),
      page.getByText(/баланс/i).first(),
    ];
    let visible = false;
    for (const loc of dashboardSignals) {
      if (await loc.isVisible().catch(() => false)) {
        visible = true;
        break;
      }
    }
    expect(visible, '/app должен показать осмысленный контент').toBe(true);
  });

  test('/app/billing — авто-пополнение НЕ падает (баг #4)', async ({ page }) => {
    await page.goto('/app/billing');

    // Баланс есть.
    await expect(page.getByTestId('billing-balance')).toBeVisible();

    // Конкретно баг #4: текст «Не удалось загрузить настройки» НЕ должен
    // появляться. Если useAutorefill() падает — этот текст показывается.
    await expect(page.getByText(/не удалось загрузить настройки/i)).toHaveCount(0);

    // Глобальный error-boundary тоже не должен сработать.
    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
  });

  test('/app/keys — список ключей рендерится', async ({ page }) => {
    await page.goto('/app/keys');

    await expect(page.getByTestId('create-key-cta')).toBeVisible();
    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
  });

  test('/app/usage — расход за период рендерится', async ({ page }) => {
    await page.goto('/app/usage');

    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
    // Период-селектор должен быть.
    await expect(page.getByTestId('usage-period-7d').or(page.getByTestId('usage-period-30d'))).toBeVisible();
  });

  test('/app/settings — настройки рендерятся, есть logout', async ({ page }) => {
    await page.goto('/app/settings');

    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
    await expect(page.getByTestId('logout-button')).toBeVisible();
    await expect(page.getByTestId('settings-tabs')).toBeVisible();
  });

  test('/app/team — командные seats рендерятся', async ({ page }) => {
    await page.goto('/app/team');

    await expect(page.getByText('Что-то пошло не так')).toHaveCount(0);
    // Кнопка «пригласить» должна быть (даже если seats пустые).
    await expect(page.getByTestId('invite-cta')).toBeVisible();
  });
});
