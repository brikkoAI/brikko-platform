/**
 * api-client Sprint 7 — routing-preferences + data-export production endpoints.
 *
 * Cross-checks MSW handlers с реальным contract:
 *  - GET /v1/account/routing-preferences возвращает full snapshot.
 *  - PUT с custom + 0 провайдеров → 400 validation_error.
 *  - PUT с smart strategy → preview обновляется в response.
 *  - GET /v1/account/data-export → array.
 *  - GET /v1/account/data-export/:id с unknown id → 404.
 *  - POST /v1/account/data-export → DataExportRequest{status:'pending'}.
 *
 * Эти тесты гоняются в Node-окружении (см. vitest.config.ts environmentMatchGlobs).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { accountApi, ApiClientError } from '@/lib/api';
import { resetFixtures } from '@/mocks/fixtures';

beforeEach(() => {
  resetFixtures();
});

describe('routing preferences API', () => {
  it('GET возвращает полный snapshot', async () => {
    const prefs = await accountApi.routingPreferences();
    expect(prefs.routing_mode).toBe('smart');
    expect(prefs.routing_strategy).toBe('cheap');
    expect(prefs.allowed_providers).toBeNull();
    expect(prefs.preview.active_models.length).toBeGreaterThan(0);
    expect(prefs.available_providers.length).toBe(6);
  });

  it('PUT custom + empty providers → 400', async () => {
    await expect(
      accountApi.updateRoutingPreferences({
        routing_mode: 'smart',
        routing_strategy: 'custom',
        allowed_providers: [],
        allowed_models: null,
      }),
    ).rejects.toBeInstanceOf(ApiClientError);
  });

  it('PUT smart strategy → preview cost обновляется', async () => {
    const prefs = await accountApi.updateRoutingPreferences({
      routing_mode: 'smart',
      routing_strategy: 'smart',
      allowed_providers: null,
      allowed_models: null,
    });
    expect(prefs.routing_strategy).toBe('smart');
    expect(prefs.preview.active_models.length).toBeGreaterThan(0);
    expect(prefs.updated_at).not.toBeNull();
  });

  it('PUT custom с 1 провайдером → preview содержит failover warning', async () => {
    const prefs = await accountApi.updateRoutingPreferences({
      routing_mode: 'smart',
      routing_strategy: 'custom',
      allowed_providers: ['yandex'],
      allowed_models: null,
    });
    expect(prefs.preview.warnings).toContain('failover_disabled_single_provider');
  });

  it('PUT non-custom + non-null providers → 400', async () => {
    await expect(
      accountApi.updateRoutingPreferences({
        routing_mode: 'smart',
        routing_strategy: 'cheap',
        allowed_providers: ['openai'],
        allowed_models: null,
      }),
    ).rejects.toBeInstanceOf(ApiClientError);
  });
});

describe('data-export production API', () => {
  it('POST → pending DataExportRequest', async () => {
    const req = await accountApi.requestDataExport();
    expect(req.id).toMatch(/^exp-/);
    expect(req.status).toBe('pending');
  });

  it('GET list возвращает массив', async () => {
    await accountApi.requestDataExport();
    const list = await accountApi.listDataExports();
    expect(Array.isArray(list)).toBe(true);
    expect(list.length).toBeGreaterThan(0);
  });

  it('GET status с неизвестным id → 404', async () => {
    await expect(accountApi.dataExportStatus('exp-unknown')).rejects.toBeInstanceOf(
      ApiClientError,
    );
  });

  it('GET status сразу после POST → pending или processing', async () => {
    const req = await accountApi.requestDataExport();
    const status = await accountApi.dataExportStatus(req.id);
    expect(['pending', 'processing']).toContain(status.status);
  });
});
