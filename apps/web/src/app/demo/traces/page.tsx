import type { Metadata } from 'next';
import { TracesMockView } from '@/components/marketing/observability-mocks';
import { DemoBanner } from '../_components/DemoBanner';

/**
 * /demo/traces — public demo страница BrikkoLens trace-лога с
 * фикстурными данными (Sprint 13.8 landing refresh, 2026-05-09).
 *
 * Не требует auth. Реальный экран — `apps/web/src/app/app/traces/page.tsx`,
 * там useTraces + фильтры + пагинация. Здесь — статичный snapshot UI.
 */

export const metadata: Metadata = {
  title: 'Демо: трейсы запросов · BrikkoLens',
  description:
    'Пример страницы /app/traces в кабинете Brikko: лог всех запросов через ваш ключ — модель, токены, latency, стоимость, статус.',
};

export default function DemoTracesPage() {
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
          <TracesMockView />
        </div>
      </section>
    </>
  );
}
