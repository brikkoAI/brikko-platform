/**
 * Sentry init для Node-runtime'а Next.js (Route Handlers, RSC, API routes).
 * Тот же DSN, что и client; разные runtime'ы — разные init'ы (так требует @sentry/nextjs).
 */
import * as Sentry from '@sentry/nextjs';

const rawDsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const dsn =
  rawDsn && !rawDsn.includes('CHANGE_ME') && !rawDsn.startsWith('${') ? rawDsn : undefined;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENV ?? 'development',
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,
    sendDefaultPii: false,
  });
}
