import type { Metadata } from 'next';
import type { Route } from 'next';
import Link from 'next/link';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: '152-ФЗ чеклист (для разработчика) · Brikko',
  description:
    'Технический раздел: настройка PII-маскинга, формат API-параметров, кастомизация категорий. Бизнес-обзор и due-diligence чеклист для compliance — на /legal/152-fz.',
  alternates: { canonical: '/docs/compliance/152-fz' },
};

/**
 * Этот раздел — DEVELOPER-DOC: настройка PII-фильтра, API-параметры,
 * кастомные категории, retention-флаги. Сейчас стоит ComingSoon, готовим
 * к Sprint 16.
 *
 * Бизнес-обзор + позиция Brikko + due-diligence чеклист (PDF) + FAQ для
 * compliance — отдельный лендинг /legal/152-fz. CTO/compliance приходит
 * туда; разработчик — сюда.
 *
 * Cross-link сверху страницы — даёт нетехническому читателю шанс
 * не залипнуть в "coming soon", а сразу уйти на нужный материал.
 */
export default function ComplianceDocPage() {
  return (
    <>
      <aside
        className="mx-auto max-w-3xl px-6 pt-10"
        aria-label="Перенаправление для compliance / CTO"
      >
        <div
          className="brikko-card-flat"
          style={{ padding: 18, background: 'var(--bg-elevated)' }}
        >
          <p className="text-body-sm text-fg-muted" style={{ lineHeight: 1.55 }}>
            Этот раздел — для разработчика, который настраивает PII-фильтр.
            Если вы compliance / CTO / юрист и нужен бизнес-обзор 152-ФЗ
            и due-diligence чеклист по AI-vendor —{' '}
            <Link
              href={'/legal/152-fz' as Route}
              className="brikko-link"
            >
              перейдите в /legal/152-fz
            </Link>
            .
          </p>
        </div>
      </aside>
      <ComingSoon
        title="152-ФЗ — чеклист соответствия (developer)"
        description="Технический гайд: как включить и настроить PII-маскинг в API-запросах, какие категории детектируются по умолчанию, как добавить кастомные паттерны, флаги retention. Бизнес-обзор для compliance-офицера и due-diligence чеклист — на /legal/152-fz."
        eta="Sprint 16"
        alternative={{
          label: 'Privacy v2 — концепция PII-маскинга',
          href: '/docs/concepts/privacy-v2' as Route,
        }}
      />
    </>
  );
}
