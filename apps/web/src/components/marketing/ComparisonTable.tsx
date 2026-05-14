import { Check, Minus } from 'lucide-react';
import type { ReactNode } from 'react';
import { BRAND } from '@/lib/brand';

/**
 * Сравнение Brikko vs ProxyAPI / VseGPT / GPTunneL / Yandex AI Studio.
 * Источник: 05_Marketing/02_landing_copy.md §5 + 04_Market/02_competitors_gateway_detailed.md.
 */
export function ComparisonTable() {
  const competitors = ['AITunnel', 'ProxyAPI', 'GPTunnel', 'Yandex AI Studio'] as const;
  type Cell = ReactNode | boolean | 'partial';

  const rows: Array<{ label: string; us: Cell; values: Cell[] }> = [
    {
      label: 'Юр. форма в РФ',
      us: 'Самозанятый (НПД), рублёвая касса',
      values: ['ИП', 'ИП', 'KZ + РФ-обвязка', 'ООО (Yandex)'],
    },
    {
      label: 'GPT, Claude, Gemini одновременно',
      us: true,
      values: [true, true, true, false],
    },
    {
      label: 'YandexGPT и GigaChat',
      us: true,
      values: [false, false, 'partial', 'Только Yandex'],
    },
    {
      label: 'Smart routing + failover',
      us: 'Да, в MVP',
      values: [false, false, false, 'partial'],
    },
    {
      label: 'Открытый прайс',
      us: 'Каждая модель в каталоге',
      values: ['Не публикуется', 'Не публикуется', 'Не публикуется', 'Свои цены'],
    },
    {
      label: 'Документы после оплаты',
      us: 'Чек НПД автоматически на email',
      values: ['Чек ОФД в кабинете', 'не публикует', 'не публикует', 'Полный пакет'],
    },
    {
      label: 'Welcome-бонус',
      us: '200 ₽',
      values: [false, false, false, false],
    },
    {
      label: 'Public status page',
      us: true,
      values: [false, false, false, 'partial'],
    },
    {
      label: 'PII-маскинг для 152-ФЗ',
      us: true,
      values: [false, false, false, false],
    },
    {
      label: 'Командные seats',
      us: true,
      values: [false, false, 'Не публично', 'IAM Yandex Cloud'],
    },
  ];

  return (
    <section className="bg-white py-16 lg:py-24">
      <div className="mx-auto max-w-6xl px-6">
        <h2 className="text-3xl font-semibold tracking-tight text-gray-900">
          Brikko vs альтернативы
        </h2>
        <p className="mt-3 text-body text-gray-500">
          Данные на 29.04.2026, из публичных тарифных страниц провайдеров. Если ваша
          информация устарела — напишите на support@brikko.ru, обновим.
        </p>

        <div className="mt-8 overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-body">
            <thead className="bg-gray-50">
              <tr>
                <th
                  scope="col"
                  className="sticky left-0 z-10 bg-gray-50 px-4 py-3 text-left text-body-sm font-medium uppercase tracking-wide text-gray-500"
                >
                  Параметр
                </th>
                {/*
                  UX-фикс 2026-05-01 (designer): шапка Brikko — bg-brand-600 + white
                  text + border-l/r/t. Раньше колонка сливалась с конкурентами при
                  bg-brand-50/40. Теперь глаз ловит «нашу» колонку за 0.5 секунды.
                */}
                <th
                  scope="col"
                  className="rounded-t-md border-x-2 border-t-2 border-brand-600 bg-brand-600 px-4 py-3 text-center text-body-sm font-semibold uppercase tracking-wide text-white"
                >
                  {BRAND.name}
                </th>
                {competitors.map((c) => (
                  <th
                    key={c}
                    scope="col"
                    className="px-4 py-3 text-center text-body-sm font-medium uppercase tracking-wide text-gray-500"
                  >
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIdx) => {
                const isLast = rowIdx === rows.length - 1;
                return (
                  <tr key={row.label} className="border-t border-gray-200">
                    <th
                      scope="row"
                      className="sticky left-0 z-10 bg-white px-4 py-3 text-left font-medium text-gray-700"
                    >
                      {row.label}
                    </th>
                    <td
                      className={`border-x-2 ${
                        isLast ? 'rounded-b-md border-b-2' : ''
                      } border-brand-600 bg-brand-50 px-4 py-3 text-center font-medium text-gray-900`}
                    >
                      {renderCell(row.us)}
                    </td>
                    {row.values.map((v, i) => (
                      <td key={i} className="px-4 py-3 text-center text-gray-600">
                        {renderCell(v)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function renderCell(value: ReactNode | boolean | 'partial') {
  if (value === true) {
    return (
      <span className="inline-flex items-center justify-center text-success-600" aria-label="Да">
        <Check className="h-5 w-5" strokeWidth={2} />
      </span>
    );
  }
  if (value === false) {
    return (
      <span className="inline-flex items-center justify-center text-gray-400" aria-label="Нет">
        <Minus className="h-5 w-5" strokeWidth={2} />
      </span>
    );
  }
  if (value === 'partial') {
    return <span className="text-warning-600">Частично</span>;
  }
  return value;
}
