import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

/**
 * MSW worker для браузера. Стартует только в dev (см. mocks/index.ts) — в production
 * клиент идёт на реальный backend через NEXT_PUBLIC_API_BASE_URL.
 */
export const worker = setupWorker(...handlers);
