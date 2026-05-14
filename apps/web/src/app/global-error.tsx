'use client';

import { useEffect } from 'react';
import * as Sentry from '@sentry/nextjs';

/**
 * Last-resort error boundary — срабатывает когда падает САМ root layout
 * (т.е. error.tsx не смог отрендериться). Должен иметь свой <html>+<body>,
 * потому что родительский layout не отрендерён (см. Next.js docs:
 * https://nextjs.org/docs/app/api-reference/file-conventions/error#global-error-handling).
 *
 * Не используем общий ErrorPage — он зависит от Tailwind-стилей через root layout.
 * Inline-стили гарантируют, что хоть что-то покажем при тотальном fail'е.
 */
export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="ru">
      <body
        style={{
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          margin: 0,
          padding: '4rem 1.5rem',
          backgroundColor: '#fafafa',
          color: '#111827',
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
        }}
      >
        <h1 style={{ fontSize: '1.5rem', margin: 0 }}>Что-то пошло не так</h1>
        <p style={{ marginTop: '0.75rem', maxWidth: '32rem', color: '#4b5563' }}>
          Попробуйте обновить страницу. Если проблема не уходит — напишите{' '}
          <a href="mailto:support@brikko.ru" style={{ color: '#2563eb' }}>
            support@brikko.ru
          </a>
          .
        </p>
        {error.digest ? (
          <p
            style={{
              marginTop: '1.5rem',
              fontFamily: 'ui-monospace, SFMono-Regular, monospace',
              fontSize: '0.75rem',
              color: '#9ca3af',
            }}
          >
            id ошибки: {error.digest}
          </p>
        ) : null}
      </body>
    </html>
  );
}
