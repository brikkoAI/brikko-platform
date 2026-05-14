import { ShieldCheck, FileLock2, Eye } from 'lucide-react';

/**
 * 152-ФЗ compliance section (Sprint 4 / V2 USP).
 *
 * UX-обоснование:
 *   - Размещаем между Features и ComparisonTable: пользователь уже понял что Brikko делает,
 *     теперь объясняем КАК это легально. Comparison после уже бьёт ProxyAPI цифрами.
 *   - Диаграмма flow в 4 шага (а не 3-stage funnel в анимации): user mental-model для PII —
 *     это «прошёл через фильтр». Анимация только отвлекает, статичные стрелки + явные тексты
 *     прямо в boxes.
 *   - 3 trust-points под диаграммой: ШИБОЛЕТЫ юр-аудита (открытый код, on-prem-режим,
 *     никакого хранения исходников). Это снимает основные возражения CISO.
 *
 * Источник копирайта: 04_Market/07_v2_monetization_research_2026-04-30.md §1 («PII-маскинг proxy»).
 */
export function PiiCompliance() {
  return (
    <section className="bg-gray-50 py-16 lg:py-24" aria-labelledby="pii-compliance-heading">
      <div className="mx-auto max-w-6xl px-6">
        <div className="max-w-3xl">
          <span className="inline-flex items-center gap-2 rounded-full bg-brand-50 px-3 py-1 text-body-sm font-medium text-brand-700">
            <ShieldCheck className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            152-ФЗ compliance
          </span>
          <h2
            id="pii-compliance-heading"
            className="mt-4 text-3xl font-semibold tracking-tight text-gray-900"
          >
            Как Brikko защищает персональные данные
          </h2>
          <p className="mt-3 text-body-large text-gray-700">
            Если твой бизнес обрабатывает ФИО, телефоны, паспорта, ИНН клиентов-граждан РФ —
            закон запрещает отправлять эти данные на серверы OpenAI/Anthropic в чистом виде.
            Brikko автоматически маскирует ПДн до того, как промпт уйдёт провайдеру, и
            восстанавливает их в ответе.
          </p>
        </div>

        <PiiFlow />

        <div className="mt-12 grid gap-6 md:grid-cols-3">
          <TrustPoint
            icon={<FileLock2 className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />}
            title="Открытая модель маскинга"
            body="Используем форк OpenAI Privacy Filter (open-source с 22.04.2026). Можно проверить,
            что и как маскируется — никаких чёрных ящиков."
          />
          <TrustPoint
            icon={<Eye className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />}
            title="Не храним промпты"
            body="По умолчанию prompt logging отключён. Включается опционально, в Settings — для
            отладки. Метаданные (токены, модель, стоимость) — нужны для счёта."
          />
          <TrustPoint
            icon={<ShieldCheck className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />}
            title="Гарантия 99.9% recall"
            body="Бенчмарк маскировщика на 1000+ корпусных записей с ФИО / email / телефонами /
            паспортами / ИНН / СНИЛС / картами. Метрика и методология открыты на /benchmarks/pii."
          />
        </div>
      </div>
    </section>
  );
}

/**
 * Flow-диаграмма: 4 шага «промпт → mask → провайдер → unmask».
 *
 * Реализована статично через Tailwind grid. Никаких canvas/SVG-анимаций — для LCP <2s
 * (BRIEF.md §11). На mobile стек становится вертикальным через `lg:grid-cols-4`.
 */
function PiiFlow() {
  const steps: Step[] = [
    {
      label: 'Твой промпт',
      example:
        '«Свяжись с Иваном Петровым +7 999 123 45 67, паспорт 4509 123456»',
      tone: 'neutral',
    },
    {
      label: 'Brikko · PII-фильтр',
      example: '«Свяжись с {NAME_1} {PHONE_1}, паспорт {DOC_1}»',
      tone: 'brand',
    },
    {
      label: 'OpenAI / Anthropic',
      example: 'Видит маскированный текст без ФИО, телефонов, документов.',
      tone: 'neutral',
    },
    {
      label: 'Ответ → unmask → ты',
      example: 'Brikko подставляет реальные значения обратно в ответ модели.',
      tone: 'success',
    },
  ];

  return (
    <ol
      className="mt-10 grid gap-4 lg:grid-cols-4"
      aria-label="Поток обработки промпта с PII-маскингом"
    >
      {steps.map((s, i) => (
        <li key={s.label} className="flex flex-col">
          <div
            className={`flex h-full flex-col gap-2 rounded-lg border p-5 ${
              s.tone === 'brand'
                ? 'border-brand-600 bg-brand-50'
                : s.tone === 'success'
                  ? 'border-success-200 bg-success-50'
                  : 'border-gray-200 bg-white'
            }`}
          >
            <span className="text-xs font-medium uppercase tracking-wide text-gray-500">
              Шаг {i + 1}
            </span>
            <p
              className={`text-body font-semibold ${
                s.tone === 'brand'
                  ? 'text-brand-700'
                  : s.tone === 'success'
                    ? 'text-success-600'
                    : 'text-gray-900'
              }`}
            >
              {s.label}
            </p>
            <p className="text-body-sm text-gray-700">{s.example}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

interface Step {
  label: string;
  example: string;
  tone: 'neutral' | 'brand' | 'success';
}

function TrustPoint({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <article className="flex flex-col gap-2 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-50 text-brand-700">
        {icon}
      </div>
      <h3 className="text-body-large font-semibold text-gray-900">{title}</h3>
      <p className="text-body-sm text-gray-700">{body}</p>
    </article>
  );
}
