import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'Биллинг и цены — концепция · Brikko',
  description: 'Документация в работе. Тарифы и цены — на /pricing.',
  alternates: { canonical: '/docs/concepts/pricing' },
};

export default function PricingConceptDocPage() {
  return (
    <ComingSoon
      title="Биллинг и цены"
      description="Как Brikko считает стоимость запроса: input/output токены, holds и расчёт после ответа, обработка частичного списания при cancelled stream'е, правила скидок за объём."
      eta="Sprint 15"
      alternative={{
        label: 'Тарифы и цены за токены',
        href: '/pricing' as Route,
      }}
    />
  );
}
