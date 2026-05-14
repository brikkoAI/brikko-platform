import { test, expect } from '@playwright/test';

/**
 * E2 — CRUD ключей через MSW. Сессия по умолчанию авторизует юзера
 * (см. mocks/fixtures.ts → TEST_SESSION_ID), поэтому /app/keys доступен сразу.
 */

test.describe('Keys CRUD', () => {
  test('создание + копирование + отзыв ключа', async ({ page }) => {
    await page.goto('/app/keys');

    // Стартовая фикстура — 2 ключа.
    await expect(page.getByTestId('keys-table')).toContainText('Production');

    // Создание
    await page.getByTestId('create-key-cta').click();
    await page.getByLabel('Имя ключа').fill('CI-tests');
    await page.getByTestId('create-key-submit').click();

    // Полный ключ показан, копирование разблокирует «Готово»
    await expect(page.getByText('Ключ создан')).toBeVisible();
    const fullKeyLocator = page.locator('div.font-mono.break-all').first();
    await expect(fullKeyLocator).toContainText(/^sk-vt-/);

    await page.getByTestId('copy-full-key').click();
    await expect(page.getByText('Скопировано')).toBeVisible();
    await page.getByRole('button', { name: 'Готово' }).click();

    // Возврат к таблице — новый ключ виден
    await expect(page.getByTestId('keys-table')).toContainText('CI-tests');
  });

  test('отзыв с typing-confirmation: кнопка disabled пока не введено имя', async ({ page }) => {
    await page.goto('/app/keys');

    // Берём существующий «Staging» — отзываем его
    await page.getByTestId('revoke-key-k-stage-002').click();

    const submit = page.getByTestId('revoke-confirm-submit');
    await expect(submit).toBeDisabled();

    await page.getByTestId('revoke-confirm-input').fill('wrong');
    await expect(submit).toBeDisabled();

    await page.getByTestId('revoke-confirm-input').fill('Staging');
    await expect(submit).toBeEnabled();
    await submit.click();

    // Toast + статус «Отозван»
    await expect(page.getByText('отозван', { exact: false })).toBeVisible();
  });
});
