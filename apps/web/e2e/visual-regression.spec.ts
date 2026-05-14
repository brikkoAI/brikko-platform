import { test, expect } from '@playwright/test';

/**
 * Sprint 4 Поток L (QA implement) — visual-regression baseline (stub).
 *
 * Status: TODO. Запускается БЕЗ snapshot'ов в текущей конфиге — каждый
 * тест помечен `test.skip` пока:
 *
 *   1. Поток I готов вернуть Storybook MVP (TD-020) — solo: deferred,
 *      ждём дизайнера или второго FE.
 *   2. Stable preview deployment (Vercel preview URL) — нужен для
 *      pixel-comparable baseline. Локальный pnpm dev даёт jitter из-за
 *      hot-reload + font-loading разницы.
 *   3. CI runner с консистентным OS (Linux x64 для baseline'ов; tests
 *      на Win/Mac будут давать diff из-за font rendering).
 *
 * Когда unblock'нуто:
 *   • убрать `test.skip` со всех 8 кейсов
 *   • запустить `npx playwright test --update-snapshots visual-regression.spec.ts`
 *     один раз для baseline
 *   • commit'нуть `e2e/visual-regression.spec.ts-snapshots/`
 *   • в CI этот файл блокирует merge на mismatch.
 *
 * См. план в Sprint 3 Поток K test_plan.md `apps/web/e2e/visual-regression.spec.ts`
 * и quality_audit_2026-04-29.md QA P1-22.
 */

test.describe('Visual regression baseline (stub — TODO Sprint 5)', () => {
  test.skip(
    !process.env.E2E_VISUAL_REGRESSION,
    'Set E2E_VISUAL_REGRESSION=1 to run; baselines TODO Sprint 5',
  );

  // 1. Marketing landing — выше fold (hero + CTA)
  test('landing hero — desktop 1280x720', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/');
    await expect(page).toHaveScreenshot('landing-hero-desktop.png', {
      fullPage: false,
      maxDiffPixelRatio: 0.01,
    });
  });

  // 2. Marketing landing — mobile
  test('landing hero — mobile 375x812', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');
    await expect(page).toHaveScreenshot('landing-hero-mobile.png', {
      fullPage: false,
      maxDiffPixelRatio: 0.01,
    });
  });

  // 3. Pricing
  test('pricing cards — desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/pricing');
    await expect(page).toHaveScreenshot('pricing-cards-desktop.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.01,
    });
  });

  // 4. Login form
  test('login form empty state', async ({ page }) => {
    await page.goto('/login');
    const form = page.getByRole('form').or(page.locator('form'));
    await expect(form).toHaveScreenshot('login-form-empty.png');
  });

  // 5. Signup form with validation errors
  test('signup form with validation errors', async ({ page }) => {
    await page.goto('/signup');
    await page.getByLabel('Email').fill('not-an-email');
    await page.getByLabel('Пароль').fill('123');
    // Trigger validation by attempting submit.
    const submit = page.getByTestId('signup-submit');
    await submit.click();
    await expect(submit.locator('..')).toHaveScreenshot('signup-validation-errors.png');
  });

  // 6. Dashboard — empty state
  test('dashboard /app — empty (no usage)', async ({ page }) => {
    // Will require seeded test session; for now just route + skip.
    test.skip(true, 'requires seeded session — TODO');
    await page.goto('/app');
    await expect(page).toHaveScreenshot('dashboard-empty.png');
  });

  // 7. Keys list — empty state
  test('keys list — empty', async ({ page }) => {
    test.skip(true, 'requires seeded session — TODO');
    await page.goto('/app/keys');
    await expect(page).toHaveScreenshot('keys-empty.png');
  });

  // 8. Forgot-password success banner
  test('forgot-password success state', async ({ page }) => {
    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill('test@voltari.test');
    await page.getByTestId('forgot-password-submit').click();
    await expect(page.getByRole('status').or(page.locator('[role="alert"]'))).toBeVisible();
    await expect(page.locator('main')).toHaveScreenshot('forgot-password-success.png');
  });
});
