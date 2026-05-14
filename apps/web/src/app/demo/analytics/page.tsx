import type { Metadata } from 'next';
import { AnalyticsMockView } from '@/components/marketing/observability-mocks';
import { DemoBanner } from '../_components/DemoBanner';

/**
 * /demo/analytics — public demo страница BrikkoLens-аналитики с
 * фикстурными KPI / графиками / breakdown'ом (Sprint 13.8 landing refresh,
 * 2026-05-09).
 *
 * Не требует auth. Реальный экран — `apps/web/src/app/app/analytics/page.tsx`.
 * Здесь — статичный snapshot UI.
 */

export const metadata: Metadata = {
  title: 'Демо: аналитика расходов · BrikkoLens',
  description:
    'Пример страницы /app/analytics в кабинете Brikko: KPI запросов, стоимости, latency и разбивка по моделям.',
};

export default function DemoAnalyticsPage() {
  return (
    <>
      <DemoBanner />
      <section
        style={{
          maxWidth: 1280,
          margin: '0 auto',
          padding: '32px 6vw 96px',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <div
          style={{
            background: 'var(--bg-base)',
            border: '1px solid var(--hairline)',
            borderRadius: 24,
            overflow: 'hidden',
          }}
        >
          <AnalyticsMockView />
        </div>
      </section>
    </>
  );
}
