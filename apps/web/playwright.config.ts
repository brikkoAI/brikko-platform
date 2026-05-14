import { defineConfig, devices } from '@playwright/test';

const PORT = 3000;
const LOCAL_BASE_URL = `http://localhost:${PORT}`;

// E2E_BASE_URL переопределяет baseURL для prod-smoke (например https://brikko.ru).
// Если задан — webServer не стартует, projects используют этот URL.
const PROD_BASE_URL = process.env.E2E_BASE_URL;
const BASE_URL = PROD_BASE_URL ?? LOCAL_BASE_URL;

const REAL_BACKEND = process.env.E2E_REAL_BACKEND === 'true';
const IS_PROD_SMOKE = Boolean(PROD_BASE_URL);

/**
 * Playwright config с двумя режимами:
 *   - default: MSW в браузере, запускаем `pnpm dev` (с моками).
 *   - E2E_REAL_BACKEND=true: пользователь сам поднял gateway + frontend
 *     с NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1. Web-server
 *     не стартуем (reuseExistingServer=true), просто проверяем что фронт жив.
 *
 * Только integration.spec.ts читает E2E_REAL_BACKEND внутри test.skip(),
 * остальные spec'и одинаково запускаются в обеих конфигах (хотя при
 * REAL_BACKEND они скорее всего сломаются на отсутствии MSW-фикстур —
 * именно поэтому `test:e2e:integration` фильтрует только integration.*).
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false, // integration.spec.ts требует serial для shared seed-account
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  // ВАЖНО: project выбирается динамически через `--project=<name>`. Default-режим
  // (без флага) запускает только 'chromium' — это сохраняет обратную совместимость
  // с существующими spec'ами (auth-real, billing, signup, ...). Дополнительные
  // именованные проекты 'local'/'prod' нужны только когда CEO/dev запускает
  // smoke вручную с --project=prod на https://brikko.ru.
  projects: [
    {
      // Default — local dev-сервер с MSW. `npm run e2e`, `test:e2e:smoke`, CI.
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], baseURL: LOCAL_BASE_URL },
    },
    {
      // Alias `--project=local` — то же что chromium, но явный. Не прогоняется
      // по умолчанию (не входит в default project list, потому что ниже
      // testIgnore исключает все spec'и).
      name: 'local',
      testMatch: /\b(01_landing_smoke|02_signup_flow|03_dashboard)\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], baseURL: LOCAL_BASE_URL },
    },
    {
      // Prod-smoke: только 01_landing_smoke. 02/03 требуют MSW и упадут на
      // реальном сайте без аутентификации. `--project=prod E2E_BASE_URL=...`.
      name: 'prod',
      testMatch: /\b01_landing_smoke\.spec\.ts$/,
      use: {
        ...devices['Desktop Chrome'],
        baseURL: PROD_BASE_URL ?? 'https://brikko.ru',
      },
    },
  ],
  // webServer не нужен когда тесты бьются в prod (E2E_BASE_URL задан) —
  // playwright не пытается поднять локальный dev-сервер.
  webServer: IS_PROD_SMOKE
    ? undefined
    : REAL_BACKEND
    ? {
        // Требуем, чтобы пользователь сам уже поднял `npm run dev` с правильным .env.local.
        // Playwright только убедится, что фронт отвечает.
        command: 'npm run dev',
        url: LOCAL_BASE_URL,
        reuseExistingServer: true,
        // 2026-05-02: 60s было мало в CI — Next.js dev-сборка с нуля без .next
        // кеша делала первый компайл 70-90с (15+ страниц с MDX/typedRoutes).
        // 180s даёт запас, локально по-прежнему стартует за ~5с.
        timeout: 300_000,  // 5 min — GitHub-hosted ubuntu-latest cold first compile 90-180s
      }
    : {
        // CI и локальная разработка используют npm (lockfile = package-lock.json,
        // pnpm не установлен в setup-node@v4 по умолчанию). См. также
        // .github/workflows/ci.yml — там тоже npm.
        //
        // 2026-05-13: переключились dev→prod. `next dev` на GitHub-hosted
        // ubuntu-latest без .next-кэша cold-compile'ил >300s (миграция с
        // self-hosted aeza-runner убрала кросс-run кэш).
        //
        // next.config.mjs использует `output: 'standalone'` (для Docker
        // multi-stage в apps/web/Dockerfile). С ним `next start` ругается
        // и не работает — нужен `node .next/standalone/server.js`.
        // Standalone-сервер требует .next/static и public/ в standalone-папке;
        // копируем их шагом CI ПОСЛЕ `next build` (см. .github/workflows/ci.yml).
        // Локально smoke E2E редко запускается; если нужно — CEO держит
        // `npm run dev` в соседнем терминале и smoke бьётся туда через
        // reuseExistingServer.
        command: 'node .next/standalone/server.js',
        url: LOCAL_BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,  // node .next/standalone/server.js стартует <5с
        env: {
          PORT: '3000',
          HOSTNAME: '0.0.0.0',
        },
      },
});
