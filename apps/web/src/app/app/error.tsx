'use client';

import { useEffect } from 'react';
import type { Route } from 'next';
import * as Sentry from '@sentry/nextjs';
import { ErrorPage } from '@/components/ErrorPage';

/**
 * Локальный error boundary для дашборда (`/app/*`). Отличается от корневого
 * `app/error.tsx` тем, что предлагает вернуться в /app, а не на лендинг —
 * у пользователя в сессии и нужный контекст не теряется.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error, { tags: { area: 'dashboard' } });
  }, [error]);

  return (
    <ErrorPage
      digest={error.digest}
      onRetry={reset}
      homeHref={'/app' as Route}
      homeLabel="Вернуться в кабинет"
    />
  );
}
