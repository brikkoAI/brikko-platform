import { IS_MOCK_BACKEND } from '@/lib/api';

/**
 * Запуск MSW worker'а в dev-режиме.
 *
 * Cleanup-инвариант (production-safety):
 *   - `process.env.NODE_ENV === 'production'` обрезает ветку → Next-bundler
 *     заменяет литералом `'production' === 'production'` и tree-shake'ает
 *     `await import('./browser')` целиком. Поэтому `msw` не попадает в production-bundle.
 *   - Когда NEXT_PUBLIC_API_BASE_URL указывает на real backend (production-домен
 *     или localhost:8000), IS_MOCK_BACKEND=false → worker не стартует даже в dev.
 *
 * Доп. защита: проверка `location.hostname` запретит MSW на любом не-локальном
 * хосте, даже если NODE_ENV почему-то не 'production' (например, preview-deploy
 * с забытым флагом).
 */
export async function startMocksIfNeeded(): Promise<void> {
  if (typeof window === 'undefined') return;
  if (process.env.NODE_ENV === 'production') return;
  if (!IS_MOCK_BACKEND) return;

  const host = window.location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host.endsWith('.local');
  if (!isLocal) return;

  const { worker } = await import('./browser');
  await worker.start({
    onUnhandledRequest: 'bypass', // не шумим по /favicon, _next, etc.
    serviceWorker: { url: '/mockServiceWorker.js' },
  });
}
