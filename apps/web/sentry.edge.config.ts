/**
 * Sentry init для edge-runtime'а Next.js (middleware.ts).
 * Минимальная конфигурация — middleware-traffic небольшой, sample-rate низкий.
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
    tracesSampleRate: 0.05,
    sendDefaultPii: false,
  });
}
