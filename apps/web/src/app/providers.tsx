'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useEffect, useState, type ReactNode } from 'react';
import { Toaster } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';

// Devtools — только в dev-сборке. Через next/dynamic с ssr:false
// гарантируем, что код не попадёт в production bundle (tree-shaken по NODE_ENV).
// См. TD-023 closure: devtools полезны для отладки cache-invalidation сценариев.
const ReactQueryDevtools =
  process.env.NODE_ENV === 'development'
    ? dynamic(
        () =>
          import('@tanstack/react-query-devtools').then((m) => ({
            default: m.ReactQueryDevtools,
          })),
        { ssr: false },
      )
    : null;

/**
 * Shared QueryClient.
 *
 * Глобальные toast'ы (402/429/5xx) и redirect на 401 живут в lib/api.ts —
 * это нижний уровень, ловит ВСЕ запросы (включая imperative-вызовы вне React Query).
 *
 * Здесь — только query-level политика retry'ев и mutation-уровневая маршрутизация
 * специфичных доменных ошибок (например, по факту удалённого ключа надо инвалидировать
 * cache — но это уже делается в самом hook'е, так что onError здесь пуст).
 */
// Запросы повторяются с экспоненциальным backoff: 1s, 2s, 4s, 8s ... cap 30s.
// Нужно чтобы кратковременная сетевая просадка/cold-start gateway не превращалась
// в немедленную ошибку UI. Cap 30s — иначе одинокий 504 на dashboard может вешать
// query на минуту, что хуже честной "ошибка, обнови страницу".
function exponentialRetryDelay(attemptIndex: number): number {
  return Math.min(1000 * 2 ** attemptIndex, 30_000);
}

// Список статусов, на которые retry бессмысленный — ответ детерминирован для
// идентичного запроса. Включая 401: к моменту когда react-query решает retry'ить,
// runRequest в lib/api.ts уже выполнил refresh-токен flow один раз. Если 401
// дошёл до сюда — рефреш не помог, retry× даст ещё N бесполезных запросов
// (раньше доходили до X3 retry × X3 на /v1/usage до hotfix TD-031). См. TD-032.
const NON_RETRYABLE_STATUSES = new Set<number>([400, 401, 403, 404, 422]);

function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiClientError) {
    if (NON_RETRYABLE_STATUSES.has(error.status)) return false;
  }
  // По умолчанию: 3 попытки на 5xx/network — этого хватает для cold-start
  // gateway (~3-5s) + одного транзиентного network glitch.
  return failureCount < 3;
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: shouldRetryQuery,
        retryDelay: exponentialRetryDelay,
        refetchOnWindowFocus: false,
      },
      mutations: {
        // Mutations НИКОГДА не retry автоматически — это was-bug в default
        // react-query (по умолчанию 0, но если глобально включить ретрай —
        // двойные top-up'ы, двойные create-key и т.п.). Идемпотентность
        // мутаций — забота вызывающего кода (Idempotency-Key для top-up).
        retry: false,
        // 402/429/5xx-toast и 401-refresh обработаны в lib/api.ts. Mutation
        // onError здесь не ставим, чтобы не дублировать тосты.
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);

  useEffect(() => {
    void import('@/mocks').then(({ startMocksIfNeeded }) => startMocksIfNeeded());
  }, []);

  return (
    <QueryClientProvider client={client}>
      {children}
      <Toaster />
      {ReactQueryDevtools ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  );
}
