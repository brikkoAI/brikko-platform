/**
 * Features — Cream Studio v6 + PII pivot (2026-05-14).
 *
 * 6 differentiator-карточек в asymmetric bento. Strict grayscale, double-bezel
 * card-styles из brikko-marketing.css. Иконки — inline SVG 1.25px stroke,
 * Phosphor-style.
 *
 * Источник копи: BRIEF_v2_pivot.md §3 (дифференциатор «152-ФЗ + Natasha NER +
 * российская инфраструктура»). Старая копия про «38 LLM / Smart Router /
 * рублёвая касса» убрана — это feature gateway, не privacy-эcosystem.
 */

type Differentiator = {
  iconKey: 'natasha' | 'reverse' | 'local' | 'entities' | 'lens' | 'shield';
  eyebrow: string;
  title: string;
  body: string;
  span: 'wide' | 'narrow';
};

// 6 карточек = 2 ряда по 12 cols (wide=8 + narrow=4). Порядок: главные
// дифференциаторы сверху (Natasha NER + российские entities + локальная
// обработка), observability + reversible unmask + 152-ФЗ — вторым рядом.
const ITEMS: Differentiator[] = [
  {
    iconKey: 'natasha',
    eyebrow: 'Natasha NER',
    title: 'Русская морфология из коробки',
    body: 'Иванов / Иванова / Иванову / о Иванове — один placeholder. Natasha NER на русском, а не машинный перевод английских моделей. ChatWall, anonym.legal и PrivacyScrubber так не умеют.',
    span: 'wide',
  },
  {
    iconKey: 'entities',
    eyebrow: 'Российские entities',
    title: 'ИНН, СНИЛС, ОГРН с checksum',
    body: 'Паспорт РФ (4+6), ИНН 10/12 цифр, СНИЛС, ОГРН/ОГРНИП, банковский счёт — все с валидацией контрольной суммы. Не ловим ложные срабатывания.',
    span: 'narrow',
  },
  {
    iconKey: 'local',
    eyebrow: 'Российский хостинг',
    title: 'Серверы в РФ, без западных карт',
    body: 'Backend на Aeza (Москва). Никакого AWS, OpenAI proxy, FX-комиссий. Соответствие требованию 152-ФЗ о локализации обработки персональных данных.',
    span: 'narrow',
  },
  {
    iconKey: 'reverse',
    eyebrow: 'Reversible unmask',
    title: 'Placeholder в LLM — оригинал в ответе',
    body: 'В ChatGPT/Claude уходит «[PERSON_1] подал заявление». Пользователю возвращается «Иванов И. И. подал заявление». LLM никогда не видит реальные ПДн, пользователь не видит маски.',
    span: 'wide',
  },
  {
    iconKey: 'lens',
    eyebrow: 'Observability',
    title: 'Видно каждое срабатывание',
    body: 'Аудит-лог запросов: какие entities нашли, какой placeholder выдали, в какой LLM ушло. Для compliance-чекапа 152-ФЗ — отдельный CSV-экспорт.',
    span: 'wide',
  },
  {
    iconKey: 'shield',
    eyebrow: '152-ФЗ',
    title: 'ПДн не уходят за границу',
    body: 'OpenAI, Anthropic, Google получают только обезличенные тексты. Запросы из РФ не выходят за границу с реальными ФИО/паспортами/ИНН. Recall 99.9% на нашем бенчмарке.',
    span: 'narrow',
  },
];

export function Features() {
  return (
    <section id="why" className="brikko-section">
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 64px',
          padding: '0 6vw',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Почему Brikko
        </span>
        <h2 className="brikko-h2">
          Чем мы отличаемся от <span className="brikko-h2-italic">западных.</span>
        </h2>
      </header>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
        className="brikko-bento"
      >
        {ITEMS.map((d) => (
          <article
            key={d.title}
            className="brikko-card-outer"
            style={{
              gridColumn: d.span === 'wide' ? 'span 8' : 'span 4',
              display: 'flex',
            }}
          >
            <div
              className="brikko-card-inner"
              style={{
                padding: '36px 32px 32px',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 220,
              }}
            >
              <div
                aria-hidden="true"
                style={{
                  width: 36,
                  height: 36,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: '1px solid var(--hairline)',
                  borderRadius: 9999,
                  color: 'var(--fg-primary)',
                  marginBottom: 24,
                }}
              >
                <Glyph iconKey={d.iconKey} />
              </div>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: '0.2em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-faint)',
                  marginBottom: 12,
                }}
              >
                {d.eyebrow}
              </div>
              <h3
                style={{
                  fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                  fontWeight: 400,
                  fontSize: 24,
                  lineHeight: 1.18,
                  letterSpacing: '-0.015em',
                  color: 'var(--fg-primary)',
                  margin: '0 0 14px',
                }}
              >
                {d.title}
              </h3>
              <p
                style={{
                  fontSize: 15,
                  lineHeight: 1.55,
                  color: 'var(--fg-muted)',
                  margin: 0,
                }}
              >
                {d.body}
              </p>
            </div>
          </article>
        ))}
      </div>

      <style>{`
        @media (max-width: 1023px) {
          .brikko-bento > article { grid-column: span 6 !important; }
        }
        @media (max-width: 767px) {
          .brikko-bento { grid-template-columns: 1fr !important; }
          .brikko-bento > article { grid-column: span 1 !important; }
        }
      `}</style>
    </section>
  );
}

function Glyph({ iconKey }: { iconKey: Differentiator['iconKey'] }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.25,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (iconKey === 'natasha') {
    // Морфология: «А» с диакритикой — символ языковой обработки.
    return (
      <svg {...common}>
        <path d="M6 19L12 5l6 14" />
        <path d="M8.5 14h7" />
        <circle cx="12" cy="3.5" r="0.9" fill="currentColor" />
      </svg>
    );
  }
  if (iconKey === 'entities') {
    // Сетка с галочкой — таблица entity-типов.
    return (
      <svg {...common}>
        <rect x="3.5" y="4" width="17" height="16" rx="1.5" />
        <path d="M3.5 9.5h17" />
        <path d="M3.5 14.5h17" />
        <path d="M9 4v16" />
        <path d="M14 4v16" />
      </svg>
    );
  }
  if (iconKey === 'local') {
    // Сервер с флагом.
    return (
      <svg {...common}>
        <rect x="4" y="4" width="16" height="6" rx="1.5" />
        <rect x="4" y="14" width="16" height="6" rx="1.5" />
        <circle cx="7" cy="7" r="0.9" fill="currentColor" />
        <circle cx="7" cy="17" r="0.9" fill="currentColor" />
        <path d="M11 7h6" />
        <path d="M11 17h6" />
      </svg>
    );
  }
  if (iconKey === 'reverse') {
    // Стрелки в обе стороны — encode/decode.
    return (
      <svg {...common}>
        <path d="M4 8h13" />
        <path d="M14 5l3 3-3 3" />
        <path d="M20 16H7" />
        <path d="M10 13l-3 3 3 3" />
      </svg>
    );
  }
  if (iconKey === 'lens') {
    return (
      <svg {...common}>
        <circle cx="12" cy="11" r="6" />
        <circle cx="12" cy="11" r="2.5" />
        <path d="M5 19h2l1.5-3 2 5 2-7 2 5 1.5-3H19" />
      </svg>
    );
  }
  // shield
  return (
    <svg {...common}>
      <path d="M12 3l8 3v6c0 4.5-3.4 8.4-8 9-4.6-.6-8-4.5-8-9V6l8-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}
