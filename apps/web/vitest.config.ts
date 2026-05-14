import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
    // jsdom-25 ставит свой AbortSignal/AbortController в globalThis, а внутренний
    // undici (Node 22+ fetch) валидирует `signal instanceof AbortSignal` против
    // СОБСТВЕННОГО Node-овского прототипа, что ломает msw 2.4 + ky на Node 22+.
    // Тесты api-client'а сетевые — DOM не нужен, гоняем их в Node-окружении.
    // jsdom оставляем для остальных (button, app-layout, login-form). См. TD-026.
    environmentMatchGlobs: [['tests/api-client*.test.ts', 'node']],
    env: {
      // В node-окружении fetch требует абсолютный URL. Резолвер api.ts
      // (resolveBaseUrl) пускает в mock-режим всё кроме абсолютной prod-ссылки,
      // а MSW handlers используют относительный path `/api/mock/v1/...`. Поэтому
      // подсовываем фейковый абсолютный origin: ENV_BASE = `http://msw-test.local/api/mock/v1`.
      // resolveBaseUrl видит непустое и не-localhost-3000 → возвращает как absolute,
      // MSW в node матчит по path → handlers срабатывают.
      NEXT_PUBLIC_API_BASE_URL: 'http://msw-test.local/api/mock/v1',
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
