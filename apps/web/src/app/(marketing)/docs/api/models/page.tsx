import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'GET /v1/models — каталог моделей · Brikko',
  description:
    'Документация в работе. Endpoint доступен — пишите support@brikko.ru за технической спецификацией.',
  alternates: { canonical: '/docs/api/models' },
};

export default function ModelsDocPage() {
  return (
    <ComingSoon
      title="Models API"
      description="GET /v1/models — список всех 38 доступных моделей: ID, контекстное окно, capabilities (tools/vision/streaming), флаг 152-ФЗ-резидентности. Используется для динамических селектов в админке клиента."
      eta="Sprint 15"
      alternative={{
        label: 'Каталог моделей с ценами',
        href: '/models' as Route,
      }}
    />
  );
}
