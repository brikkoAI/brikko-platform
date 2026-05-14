/**
 * Sentry init для browser-runtime'а Next.js.
 *
 * Контракт:
 *   - Если NEXT_PUBLIC_SENTRY_DSN не задан → init НЕ вызывается, captureException no-op.
 *     Это допустимое состояние для local dev и self-launch без подключённого Sentry.
 *   - PII (email, токены, тело payment-запросов) фильтруются в beforeSend.
 *   - tracesSampleRate=0.1 в проде, 1.0 в dev — чтобы не съедать quota на каждый клик.
 *
 * Файл подхватывается автоматически через @sentry/nextjs build-плагин (см. next.config.mjs).
 */
import * as Sentry from '@sentry/nextjs';

const rawDsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
// Защита от случаев, когда .env содержит "${SENTRY_DSN_WEB}" (substitution не работает в .env)
// или плейсхолдер "CHANGE_ME". Считаем такой DSN отсутствующим.
const dsn =
  rawDsn && !rawDsn.includes('CHANGE_ME') && !rawDsn.startsWith('${') ? rawDsn : undefined;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENV ?? 'development',
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,
    // Не отправляем PII по умолчанию.
    sendDefaultPii: false,
    beforeSend(event) {
      // Чистим request.data: email/password/токены не должны попасть в payload Sentry.
      // (Sentry SDK сам сериализует тело fetch'а, если мы не вмешаемся.)
      if (event.request?.data && typeof event.request.data === 'object') {
        const data = event.request.data as Record<string, unknown>;
        for (const key of Object.keys(data)) {
          if (/email|password|token|secret|key|otp|code/i.test(key)) {
            data[key] = '[redacted]';
          }
        }
      }
      // breadcrumbs тоже могут содержать URL с email в querystring.
      if (event.breadcrumbs) {
        event.breadcrumbs = event.breadcrumbs.map((b) => {
          if (b.data && typeof b.data === 'object') {
            const dataObj = b.data as Record<string, unknown>;
            if (typeof dataObj.url === 'string' && /email=|token=/.test(dataObj.url)) {
              dataObj.url = dataObj.url.replace(/(email|token)=[^&]+/g, '$1=[redacted]');
            }
          }
          return b;
        });
      }
      return event;
    },
  });
} else if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
  // В dev предупредим, чтобы не было сюрпризов «почему ошибки не приходят». В prod молчим
  // — релиз без Sentry — допустимое решение, не warning.
  // eslint-disable-next-line no-console -- dev-only diagnostic
  console.warn('[sentry] NEXT_PUBLIC_SENTRY_DSN not configured — error tracking disabled');
}
