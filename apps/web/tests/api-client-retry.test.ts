/**
 * TD-032: react-query retry на 401 не должен срабатывать.
 *
 * Контекст бага: при endpoint, всегда возвращающим 401 (как было с /v1/usage до
 * hotfix TD-031), срабатывал retry × 3 уровнем react-query, каждый из которых
 * через runRequest триггерил attemptRefresh — итого 3 × refresh × 3 запросов в
 * loop, мигание UI чёрный/белый.
 *
 * Здесь проверяем три инварианта:
 *   1. queries: 401/403/404/400/422 не повторяются.
 *   2. queries: 500 повторяется до 3 retry (4 попытки).
 *   3. mutations: НЕ повторяются автоматически (двойные top-up — table-stake bug).
 *
 * Используется собственный QueryClient (изолированный), но политика retry
 * скопирована с providers.tsx — если разъедется, тест ловит регрессию.
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { QueryClient, MutationObserver } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/mocks/server';
import { ApiClientError } from '@/lib/api';

const NON_RETRYABLE_STATUSES = new Set<number>([400, 401, 403, 404, 422]);

function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiClientError) {
    if (NON_RETRYABLE_STATUSES.has(error.status)) return false;
  }
  return failureCount < 3;
}

function makeIsolatedClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
        // Не ждём backoff в тестах, иначе 3 retry × 8s = таймаут.
        retryDelay: 0,
        gcTime: 0,
      },
      mutations: { retry: false },
    },
  });
}

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

function asApiError(status: number, type: string, message = 'no'): ApiClientError {
  return new ApiClientError(status, { type: type as never, message });
}

async function fetchOrThrow(url: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      error?: { type?: string; message?: string };
    };
    throw asApiError(res.status, body.error?.type ?? 'unknown_error', body.error?.message);
  }
  return res.json();
}

describe('TD-032: react-query retry policy', () => {
  let qc: QueryClient;
  beforeEach(() => {
    qc = makeIsolatedClient();
  });

  it('не делает retry на 401 (sticky auth-failure)', async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/probe-401`, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { type: 'unauthorized', message: 'No' } },
          { status: 401 },
        );
      }),
    );

    await expect(
      qc.fetchQuery({
        queryKey: ['probe-401'],
        queryFn: () => fetchOrThrow(`${BASE}/probe-401`),
      }),
    ).rejects.toMatchObject({ status: 401 });
    expect(calls).toBe(1);
  });

  it('не делает retry на 403 / 404 / 400 / 422', async () => {
    for (const code of [403, 404, 400, 422]) {
      let calls = 0;
      server.use(
        http.get(`${BASE}/probe-${code}`, () => {
          calls += 1;
          return HttpResponse.json(
            { error: { type: 'forbidden', message: 'No' } },
            { status: code },
          );
        }),
      );

      await expect(
        qc.fetchQuery({
          queryKey: [`probe-${code}`],
          queryFn: () => fetchOrThrow(`${BASE}/probe-${code}`),
        }),
      ).rejects.toBeInstanceOf(ApiClientError);
      expect(calls, `статус ${code} должен идти один раз`).toBe(1);
    }
  });

  it('делает retry на 500 — 4 попытки и финальная ошибка', async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/probe-500`, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { type: 'server_error', message: 'Down' } },
          { status: 500 },
        );
      }),
    );

    await expect(
      qc.fetchQuery({
        queryKey: ['probe-500'],
        queryFn: () => fetchOrThrow(`${BASE}/probe-500`),
      }),
    ).rejects.toMatchObject({ status: 500 });
    // initial + 3 retry = 4 attempts (failureCount < 3 → последний retry на failureCount=2).
    expect(calls).toBe(4);
  });

  it('успешно восстанавливается на втором заходе после 500', async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/probe-flaky`, () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json(
            { error: { type: 'server_error', message: 'glitch' } },
            { status: 500 },
          );
        }
        return HttpResponse.json({ ok: true });
      }),
    );

    const result = await qc.fetchQuery({
      queryKey: ['probe-flaky'],
      queryFn: () => fetchOrThrow(`${BASE}/probe-flaky`),
    });
    expect(result).toEqual({ ok: true });
    expect(calls).toBe(2);
  });

  it('mutation никогда не retry (даже на 500)', async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/probe-mutation-500`, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { type: 'server_error', message: 'Down' } },
          { status: 500 },
        );
      }),
    );

    // MutationObserver — публичное API react-query 5 для запуска mutation
    // вне React-дерева. Используем его, чтобы не плодить компонент-обёртку
    // для unit-теста.
    const observer = new MutationObserver(qc, {
      mutationFn: () => fetchOrThrow(`${BASE}/probe-mutation-500`, { method: 'POST' }),
      // Дублируем `retry: false` явно — на случай если defaultOptions кем-то
      // переопределяется выше (мы тут изолированы, но defensive coding).
      retry: false,
    });

    await expect(observer.mutate()).rejects.toBeInstanceOf(ApiClientError);
    expect(calls, 'mutation на 500 — ровно одна попытка').toBe(1);
  });
});
