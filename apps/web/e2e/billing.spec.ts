import { test, expect } from '@playwright/test';

test.describe('Billing topup flow (mock ЮKassa)', () => {
  test('пополнение через preset → баланс растёт', async ({ page }) => {
    await page.goto('/app/billing');

    const balanceBefore = await page.getByTestId('billing-balance').textContent();
    expect(balanceBefore).toContain('1 000');

    // Выбираем preset 5 000 ₽ и идём на mock-confirmation URL
    await page.getByRole('button', { name: '5 000 ₽' }).click();
    await page.getByTestId('topup-submit').click();

    // Mock ЮKassa возвращает relative URL с topup=success — landing на /app/billing
    await expect(page).toHaveURL(/topup=success/);
    await expect(page.getByText('Платёж принят')).toBeVisible();
    await expect(page.getByTestId('billing-balance')).toContainText('6 000');
  });
});
