import { test, expect } from '@playwright/test';

/**
 * E2E happy path. Backend заменён MSW worker'ом — стартует автоматически в dev (см. providers.tsx).
 * Никаких page.route() — работаем поверх честного MSW, как реальный пользователь.
 */

test.describe('Signup → verify-email → first key → curl', () => {
  test('пользователь регистрируется, подтверждает email и попадает в /app с welcome-балансом', async ({
    page,
  }) => {
    const email = `e2e-${Date.now()}@example.com`;

    await page.goto('/signup');
    await expect(page.getByRole('heading', { name: 'Создать аккаунт' })).toBeVisible();

    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Пароль').fill('verysecure-password-1');
    await page.getByRole('checkbox').check();
    await page.getByTestId('signup-submit').click();

    await expect(page).toHaveURL(/\/signup\/verify-email/);
    await expect(page.getByRole('heading', { name: 'Проверь почту' })).toBeVisible();

    // Симулируем переход по ссылке из письма (DEV-link использует email в качестве token).
    await page.getByTestId('dev-verify-link').click();

    // После verify редирект на /app + начислены 200 ₽ welcome.
    await expect(page).toHaveURL('/app');
    await expect(page.getByTestId('topbar-balance')).toContainText('200');
  });

  test('показывает ошибки валидации при пустой форме', async ({ page }) => {
    await page.goto('/signup');
    await page.getByTestId('signup-submit').click();

    await expect(page.getByText('Введи email')).toBeVisible();
    await expect(page.getByText('Минимум 10 символов')).toBeVisible();
    await expect(page.getByText(/принять условия/)).toBeVisible();
  });
});
