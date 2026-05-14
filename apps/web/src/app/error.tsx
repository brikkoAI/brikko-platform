'use client';

import { useEffect } from 'react';
import * as Sentry from '@sentry/nextjs';
import { ErrorPage } from '@/components/ErrorPage';

/**
 * Корневой error boundary для всего app/. Ловит ошибки из любого route segment
 * (кроме самого root layout — для него работает `global-error.tsx`).
 *
 * Sentry.captureException вызывается ровно один раз — на mount, через useEffect.
 * Если DSN не сконфигурирован, captureException — no-op (см. sentry.client.config.ts).
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return <ErrorPage digest={error.digest} onRetry={reset} />;
}
