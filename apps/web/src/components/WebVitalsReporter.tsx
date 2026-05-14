'use client';

import * as Sentry from '@sentry/nextjs';
import { useReportWebVitals } from 'next/web-vitals';

/**
 * Reports Core Web Vitals as Sentry breadcrumbs (NOT events) — добавляет
 * контекст к любому будущему error-event'у того же сеанса.
 *
 * Почему breadcrumb, а не отдельный capture / metric:
 *   - Sentry metrics (Sentry.metrics.distribution) — beta, требует sample-config
 *     отдельно, и ест quota на каждый клиентский визит.
 *   - Breadcrumb бесплатный — он дописывается ТОЛЬКО когда error-event и так
 *     отправляется. Ты не видишь LCP отдельной диаграммой, но видишь
 *     «у юзера, у которого упал /app/billing, LCP был 4.8s» — что обычно
 *     полезнее для отладки.
 *
 * Поддерживаются Next 14-стандартные метрики:
 *   - FCP, LCP, CLS, INP (Interaction to Next Paint, заменил FID в Web Vitals 4.0)
 *   - TTFB
 *
 * Также пишем в performance.measure (для Chrome DevTools / Lighthouse визуализации).
 *
 * Не блокирует SSR — только client-side (`use client`). Имя — отчёт о метрике,
 * не критическая логика; никаких throw'ов наверх.
 */
export function WebVitalsReporter() {
  useReportWebVitals((metric) => {
    try {
      Sentry.addBreadcrumb({
        category: 'web-vital',
        level: 'info',
        message: `${metric.name} ${Math.round(metric.value)}ms`,
        data: {
          name: metric.name,
          value: metric.value,
          rating: 'rating' in metric ? metric.rating : undefined,
          id: metric.id,
          navigationType: 'navigationType' in metric ? metric.navigationType : undefined,
        },
      });

      // performance.mark — даёт DevTools отображение метрики на timeline.
      // Тихо ноп при отсутствии (старые браузеры / SSR).
      if (typeof performance !== 'undefined' && typeof performance.mark === 'function') {
        performance.mark(`web-vital:${metric.name}`, {
          detail: { value: metric.value },
        });
      }
    } catch {
      // Reporter — всегда best-effort. Если Sentry не инициализирован
      // (нет DSN) или performance API отвалился — молча пропускаем.
    }
  });

  return null;
}
