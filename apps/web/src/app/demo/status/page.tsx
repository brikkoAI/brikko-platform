import type { Metadata } from 'next';
import { StatusMockView } from '@/components/marketing/observability-mocks';
import { DemoBanner } from '../_components/DemoBanner';

/**
 * /demo/status — public demo страница single-pane platform status
 * с фикстурным светофором, KPI и провайдерами (Sprint 13.8 landing refresh,
 * 2026-05-09).
 *
 * Не требует auth. Реальный экран — `apps/web/src/app/app/status/page.tsx`
 * (admin-only там). Здесь — статичный snapshot UI для marketing'а.
 */

export const metadata: Metadata = {
  title: 'Демо: статус платформы · Brikko',
  description:
    'Пример страницы /app/status: реальное состояние API, провайдеров, инфраструктуры и трафика за 24 часа.',
};

export default function DemoStatusPage() {
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
          <StatusMockView />
        </div>
      </section>
    </>
  );
}
