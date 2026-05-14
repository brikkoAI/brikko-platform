/**
 * FE P0-5: CSRF module — final integration tests.
 *
 * После публикации `docs/csrf_protocol.md` (Поток D, Sprint 2) `getCsrfToken`
 * делает реальный `GET /v1/auth/csrf` через MSW handler. Тесты проверяют:
 *   - bootstrap (fetch + cache),
 *   - single-flight (5 параллельных вызовов = 1 fetch),
 *   - clearCsrfToken инвалидирует кэш → следующий вызов делает свежий fetch,
 *   - setCsrfTokenFromResponse пишет в кэш напрямую (login/refresh path),
 *   - __setCsrfTokenForTest для прямой инжекции.
 *
 * MSW handler в `src/mocks/handlers.ts` отдаёт стабильный mock-токен
 * `mock-csrf-token-32chars-stable-aaa` — изолируем тесты от реального
 * backend'а.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import {
  getCsrfToken,
  setCsrfTokenFromResponse,
  clearCsrfToken,
  __setCsrfTokenForTest,
  CSRF_HEADER_NAME,
  LEGACY_CSRF_HEADER_NAME,
  LEGACY_CSRF_VALUE,
} from '@/lib/csrf';

const MOCK_CSRF_TOKEN = 'mock-csrf-token-32chars-stable-aaa';

describe('csrf module', () => {
  beforeEach(() => {
    clearCsrfToken();
  });

  it('экспортирует header constants', () => {
    expect(CSRF_HEADER_NAME).toBe('X-CSRF-Token');
    expect(LEGACY_CSRF_HEADER_NAME).toBe('X-Requested-With');
    expect(LEGACY_CSRF_VALUE).toBe('voltari-web');
  });

  it('getCsrfToken делает bootstrap через GET /auth/csrf', async () => {
    const t = await getCsrfToken();
    expect(t).toBe(MOCK_CSRF_TOKEN);
  });

  it('getCsrfToken кэширует — два вызова не дублируют network round-trip', async () => {
    const t1 = await getCsrfToken();
    const t2 = await getCsrfToken();
    expect(t1).toBe(t2);
    expect(t1).toBe(MOCK_CSRF_TOKEN);
  });

  it('clearCsrfToken инвалидирует кэш — следующий вызов делает свежий fetch', async () => {
    __setCsrfTokenForTest('explicit-test-token');
    const before = await getCsrfToken();
    expect(before).toBe('explicit-test-token');

    clearCsrfToken();
    const after = await getCsrfToken();
    // После clear → fresh fetch → MSW отдаёт стабильный mock-токен.
    expect(after).toBe(MOCK_CSRF_TOKEN);
  });

  it('параллельные getCsrfToken не плодят race (single-flight)', async () => {
    clearCsrfToken();
    const [a, b, c] = await Promise.all([getCsrfToken(), getCsrfToken(), getCsrfToken()]);
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(a).toBe(MOCK_CSRF_TOKEN);
  });

  it('setCsrfTokenFromResponse пишет в кэш напрямую (login/refresh path)', async () => {
    setCsrfTokenFromResponse({ csrf_token: 'fresh-from-login-response' });
    const t = await getCsrfToken();
    expect(t).toBe('fresh-from-login-response');
  });

  it('setCsrfTokenFromResponse игнорирует пустой/undefined токен', async () => {
    __setCsrfTokenForTest('existing-token');
    setCsrfTokenFromResponse({ csrf_token: undefined });
    expect(await getCsrfToken()).toBe('existing-token');

    setCsrfTokenFromResponse({ csrf_token: '' });
    expect(await getCsrfToken()).toBe('existing-token');
  });

  it('__setCsrfTokenForTest позволяет инжектировать токен напрямую', async () => {
    __setCsrfTokenForTest('injected-XYZ');
    expect(await getCsrfToken()).toBe('injected-XYZ');
  });
});
