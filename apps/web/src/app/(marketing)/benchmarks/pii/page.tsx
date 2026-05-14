import Link from 'next/link';

export const metadata = {
  title: 'PII-маскинг бенчмарк — Brikko',
  description:
    'Открытая метрика recall PII-маскера Brikko на golden corpus 50 документов. 100% по 8 структурированным категориям. Гарантия возврата средств при пропуске.',
};

/**
 * /benchmarks/pii — публичный отчёт о метрике recall PII-маскера.
 *
 * Источник цифр: apps/gateway/tests/pii_corpus/benchmark_results/2026-05-09.json
 * (snapshot последнего прогона на 9 мая 2026). Корпус — 50 hand-annotated
 * документов в 6 жанрах, 100% синтетика на проверяемых шаблонах. Полный код
 * бенчмарка, корпуса и валидатора — в репозитории gateway.
 *
 * Сервер-side рендер без client-state — это trust-signal, не интерактив.
 */

interface CategoryRow {
  key: string;
  label: string;
  description: string;
  tp: number;
  fn: number;
  recall: number;
  guarantee: 'gold' | 'silver';
}

// Snapshot from benchmark_results/2026-05-09.json. Update on re-snapshot.
const RESULTS: readonly CategoryRow[] = [
  {
    key: 'EMAIL',
    label: 'Email',
    description: 'Адреса электронной почты в любом формате RFC 5322.',
    tp: 24,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'PHONE',
    label: 'Телефоны',
    description: '+7 / 8 / 7 / без кода, со скобками, дефисами, пробелами.',
    tp: 27,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'PASSPORT',
    label: 'Паспорт РФ',
    description: 'Серия + номер: «4509 123456», «4509-123456», «4509123456».',
    tp: 13,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'INN',
    label: 'ИНН',
    description: '10/12-знаков с проверкой контрольной суммы.',
    tp: 21,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'SNILS',
    label: 'СНИЛС',
    description: '11-знаков с проверкой контрольной суммы.',
    tp: 15,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'OGRN',
    label: 'ОГРН',
    description: '13-знаков юр.лица с проверкой контрольной суммы.',
    tp: 5,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'OGRNIP',
    label: 'ОГРНИП',
    description: '15-знаков ИП с проверкой контрольной суммы.',
    tp: 3,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'BANK_ACCOUNT',
    label: 'Банковский счёт',
    description: '20-знаков расчётного счёта с проверкой ключа.',
    tp: 7,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'CARD',
    label: 'Банковская карта',
    description: 'PAN 13-19 знаков с проверкой Луна.',
    tp: 4,
    fn: 0,
    recall: 1.0,
    guarantee: 'gold',
  },
  {
    key: 'NAME',
    label: 'ФИО',
    description: 'Имена, фамилии, отчества — kyrillic morphology через Natasha.',
    tp: 47,
    fn: 6,
    recall: 0.8868,
    guarantee: 'silver',
  },
];

const SNAPSHOT_DATE = '2026-05-09';
const CORPUS_SIZE = 50;
const CORPUS_GENRES = ['contract', 'business_letter', 'call_transcript', 'bank_statement', 'job_application', 'informal_chat'];

export default function PiiBenchmarksPage() {
  const goldRows = RESULTS.filter((r) => r.guarantee === 'gold');
  const silverRows = RESULTS.filter((r) => r.guarantee === 'silver');
  const goldTotalTp = goldRows.reduce((sum, r) => sum + r.tp, 0);
  const goldTotalFn = goldRows.reduce((sum, r) => sum + r.fn, 0);
  const goldRecall = goldTotalTp / (goldTotalTp + goldTotalFn);

  return (
    <div className="brikko-marketing">
      <section className="brikko-section">
        <div style={{ maxWidth: 880, margin: '0 auto', padding: '64px 6vw 96px' }}>
          <span className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Открытый бенчмарк
          </span>
          <h1 className="brikko-h2" style={{ marginTop: 16 }}>
            PII-маскинг.{' '}
            <span className="brikko-h2-italic">Recall с гарантией.</span>
          </h1>
          <p className="brikko-lede" style={{ marginTop: 24, maxWidth: 720 }}>
            На тарифе Pro Privacy перед отправкой во внешние LLM (OpenAI, Anthropic, Google) Brikko
            заменяет 9 категорий персональных данных на токены. Это таблица recall (полнота
            обнаружения) на нашем golden corpus — обновляется при каждом релизе маскера.
          </p>

          <div
            style={{
              marginTop: 40,
              padding: '24px 28px',
              borderRadius: 12,
              background: 'var(--bg-elevated)',
              border: '1px solid var(--hairline)',
            }}
          >
            <p
              style={{
                margin: 0,
                fontSize: 14,
                color: 'var(--fg-muted)',
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              }}
            >
              Snapshot {SNAPSHOT_DATE} · golden corpus {CORPUS_SIZE} doc · {CORPUS_GENRES.length}{' '}
              жанров
            </p>
            <p style={{ marginTop: 8, marginBottom: 0, fontSize: 36, fontFamily: '"Source Serif 4", Georgia, serif', fontWeight: 400, lineHeight: 1.1 }}>
              <strong style={{ fontVariantNumeric: 'tabular-nums' }}>
                {(goldRecall * 100).toFixed(2)}%
              </strong>{' '}
              <span style={{ fontSize: 16, color: 'var(--fg-muted)', fontFamily: 'inherit' }}>
                recall на 9 структурированных категориях ({goldTotalTp}/{goldTotalTp + goldTotalFn}{' '}
                spans)
              </span>
            </p>
          </div>

          <h2
            style={{
              marginTop: 72,
              fontSize: 14,
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              textTransform: 'uppercase',
              letterSpacing: '0.15em',
              color: 'var(--fg-muted)',
              fontWeight: 500,
            }}
          >
            Гарантия 99.9% — 9 структурированных категорий
          </h2>
          <p style={{ marginTop: 12, fontSize: 16, color: 'var(--fg-primary)', maxWidth: 720 }}>
            Если на ваших данных выявится пропуск маскинга в одной из категорий ниже — возвращаем
            стоимость месячной подписки Pro Privacy. Условие закреплено в публичной оферте.
          </p>

          <div style={{ marginTop: 24, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--hairline)' }}>
                  <th style={{ textAlign: 'left', padding: '12px 16px 12px 0', fontWeight: 500, color: 'var(--fg-muted)' }}>Категория</th>
                  <th style={{ textAlign: 'left', padding: '12px 16px', fontWeight: 500, color: 'var(--fg-muted)' }}>Описание</th>
                  <th style={{ textAlign: 'right', padding: '12px 16px', fontWeight: 500, color: 'var(--fg-muted)', fontVariantNumeric: 'tabular-nums' }}>Найдено</th>
                  <th style={{ textAlign: 'right', padding: '12px 0 12px 16px', fontWeight: 500, color: 'var(--fg-muted)', fontVariantNumeric: 'tabular-nums' }}>Recall</th>
                </tr>
              </thead>
              <tbody>
                {goldRows.map((r) => (
                  <tr key={r.key} style={{ borderBottom: '1px solid var(--hairline)' }}>
                    <td style={{ padding: '14px 16px 14px 0', fontWeight: 500 }}>{r.label}</td>
                    <td style={{ padding: '14px 16px', color: 'var(--fg-muted)' }}>{r.description}</td>
                    <td style={{ padding: '14px 16px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--fg-muted)' }}>
                      {r.tp} / {r.tp + r.fn}
                    </td>
                    <td style={{ padding: '14px 0 14px 16px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontFamily: 'Geist Mono, ui-monospace, monospace' }}>
                      {(r.recall * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2
            style={{
              marginTop: 72,
              fontSize: 14,
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              textTransform: 'uppercase',
              letterSpacing: '0.15em',
              color: 'var(--fg-muted)',
              fontWeight: 500,
            }}
          >
            Best-effort — имена ФИО
          </h2>
          <p style={{ marginTop: 12, fontSize: 16, color: 'var(--fg-primary)', maxWidth: 720 }}>
            ФИО ловятся через ML-распознаватель (Natasha) — это даёт вариативность и работает на
            склонениях, но не гарантирует абсолютные 100%. На корпусе ниже recall {(silverRows[0]!.recall * 100).toFixed(1)}%; типовые
            промахи — конструкции типа «Уважаемый Сидоров» (склейка с приветствием) и редкие
            многословные ФИО разбиваются на два span&apos;а вместо одного. На имена гарантия
            возврата не распространяется — это явно зафиксировано в оферте.
          </p>
          <p style={{ marginTop: 16, fontSize: 14, color: 'var(--fg-muted)', maxWidth: 720 }}>
            План улучшения: переход с Natasha-yargy на Slovnet (BERT-based RU NER) в Q3 2026 —
            ожидаемый recall ≥97%. Прогресс отслеживаем в этой же таблице.
          </p>

          <div style={{ marginTop: 24, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
              <tbody>
                {silverRows.map((r) => (
                  <tr key={r.key}>
                    <td style={{ padding: '14px 16px 14px 0', fontWeight: 500 }}>{r.label}</td>
                    <td style={{ padding: '14px 16px', color: 'var(--fg-muted)' }}>{r.description}</td>
                    <td style={{ padding: '14px 16px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--fg-muted)' }}>
                      {r.tp} / {r.tp + r.fn}
                    </td>
                    <td style={{ padding: '14px 0 14px 16px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontFamily: 'Geist Mono, ui-monospace, monospace' }}>
                      {(r.recall * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2
            style={{
              marginTop: 72,
              fontSize: 14,
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              textTransform: 'uppercase',
              letterSpacing: '0.15em',
              color: 'var(--fg-muted)',
              fontWeight: 500,
            }}
          >
            Методология
          </h2>
          <ul style={{ marginTop: 12, paddingLeft: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 12, color: 'var(--fg-primary)', maxWidth: 720, fontSize: 15, lineHeight: 1.6 }}>
            <li>
              <strong>Корпус:</strong> 50 синтетических документов с hand-annotated character-offset
              spans. 6 жанров: договоры, деловые письма, расшифровки звонков, выписки, резюме,
              чаты. Все ФИО / телефоны / паспорта — выдуманные; ИНН / СНИЛС / ОГРН — KC-валидные
              (проверяется автоматически).
            </li>
            <li>
              <strong>Метрика:</strong> char-offset overlap. Найденный span засчитывается true-positive
              если пересечение с gold-span ≥80%. False-negative — gold-span без пересечений.
            </li>
            <li>
              <strong>Воспроизводимость:</strong> код корпуса детерминированный, валидатор проверяет
              что каждый annotation slice&apos;ит обратно в значение и что checksum валиден.
            </li>
            <li>
              <strong>CI gate:</strong> на каждый PR прогоняется бенчмарк, regression выше 0.5pp по
              recall блокирует merge.
            </li>
            <li>
              <strong>Ограничения:</strong> upper bound на real-world recall может отличаться. Этот
              корпус — regression detector, не absolute quality measurement. На критичных данных
              рекомендуем дополнительный preprocessing на стороне клиента.
            </li>
          </ul>

          <div style={{ marginTop: 56, padding: '24px 28px', borderRadius: 12, border: '1px solid var(--hairline)', background: 'var(--bg-elevated)' }}>
            <p style={{ margin: 0, fontSize: 14, color: 'var(--fg-muted)', fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace' }}>
              Что дальше
            </p>
            <p style={{ marginTop: 8, marginBottom: 16, fontSize: 16, color: 'var(--fg-primary)' }}>
              Подробнее о маскинге — в документации. Условия гарантии — в публичной оферте.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              <Link href="/docs/concepts/privacy-v2" className="brikko-btn brikko-btn-primary">
                Документация PII-маскера
              </Link>
              <Link href="/legal/oferta" className="brikko-btn brikko-btn-secondary">
                Оферта
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
