import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config для **реального backend'а** (gateway + postgres + redis).
 *
 * Назначение:
 *   - Ловить класс багов, которые MSW-моки физически не поймают: NEXT_PUBLIC
 *     ушёл в моки, prerender валится в prod-build, dual-auth не работает,
 *     CSRF-mismatch, ROUND_TRIP refresh-flow.
 *   - Запуск против locally-built production-сборки фронта (НЕ `next dev` с
 *     hot-reload, потому что dev-mode маскирует prod-only ошибки сборки).
 *
 * Стек запуска (управляется `npm run test:e2e:real`):
 *   1. Внешний скрипт поднимает gateway + postgres + redis через
 *      `docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --wait`.
 *   2. Внешний скрипт прогоняет `alembic upgrade head` внутри gateway-контейнера.
 *   3. Playwright стартует web в production-build режиме (`next build && next start`)
 *      с `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1`.
 *   4. Тесты бьются о реальный backend; cleanup БД между тестами через
 *      `docker exec voltari-postgres psql -c "TRUNCATE ... CASCADE"`.
 *
 * Тесты тут НЕ входят в дефолтный `npm run test:e2e` — поднять docker долго
 * и шумно. Запускаются отдельно перед каждым релизом + как часть
 * `smoke.yml` после deploy на staging.
 *
 * Использование:
 *   ```
 *   # 1. поднять backend
 *   docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --wait gateway postgres redis
 *   docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml run --rm gateway alembic upgrade head
 *
 *   # 2. собрать web в prod-mode и запустить
 *   cd apps/web
 *   NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1 npm run build
 *
 *   # 3. прогнать тесты
 *   npx playwright test --config=playwright.config.real.ts
 *   ```
 */

const PORT = 3000;
const BASE_URL = `http://localhost:${PORT}`;
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/v1';

export default defineConfig({
  testDir: './e2e',
  // Real-backend тесты — отдельная подпапка/паттерн, чтобы не пересекаться
  // с MSW-тестами из обычного `playwright.config.ts`.
  testMatch: /.*-real\.spec\.ts$/,

  // Real-backend медленнее: docker-сеть, прогрев процесса.
  timeout: 60_000,
  expect: { timeout: 10_000 },

  // serial — в тестах общий postgres, чистка БД между тестами через
  // beforeEach. Параллель ломает изоляцию.
  fullyParallel: false,
  workers: 1,

  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    extraHTTPHeaders: {
      // Backend требует X-Requested-With на POST/PATCH/DELETE для cookie-сессий.
      // Браузер ставит его автоматически на ky-запросах (см. apps/web/src/lib/api.ts:116),
      // но прямые page.request.post() — нет; экстра-header страхует.
      'X-Requested-With': 'voltari-web',
    },
  },

  projects: [
    {
      name: 'chromium-real-backend',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    // PRODUCTION-build, НЕ `next dev`. Это критично: dev-mode не
    // запекает NEXT_PUBLIC_* в bundle, а именно эту проблему мы и ловим.
    //
    // `npm run build` нужно прогнать отдельно ДО запуска тестов с правильным
    // NEXT_PUBLIC_API_BASE_URL — оно прибивается к bundle при build, а не start.
    command: 'npm run start',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_BASE_URL: API_BASE_URL,
      NEXT_PUBLIC_FRONTEND_URL: BASE_URL,
      NODE_ENV: 'production',
      PORT: String(PORT),
    },
  },
});
