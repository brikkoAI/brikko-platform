import Link from 'next/link';
import type { Route } from 'next';

/**
 * CookbookTeaser — Cream Studio v6 (Sprint 13, 2026-05-02).
 *
 * 3 готовых рецепта (legal / CRM / support) — strict grayscale, double-bezel
 * cards, tag pills. Каждая карточка — link на /cookbook/<slug>.
 *
 * UX-обоснование: разработчик после Hero и Models хочет «покажите код».
 * Cookbook — это конкретные сценарии, цена-в-рублях, готовый prompt. Снимает
 * абстрактность раздела «what we do» и переводит в «вот как это применяется».
 */

type Recipe = {
  title: string;
  tags: string[];
  cost: string;
  description: string;
  href: string;
};

const RECIPES: Recipe[] = [
  {
    title: 'Юристы — анализ договора с PII-маскингом',
    tags: ['legal', 'pii', 'claude'],
    cost: '~980 ₽ / 1000 запросов',
    description:
      'Прогнал договор через Claude Sonnet 4.6 с автозаменой ФИО / телефонов. Strict JSON Schema на выходе.',
    href: '/cookbook/contract-parsing',
  },
  {
    title: 'CRM — классификация лидов hot / warm / cold',
    tags: ['crm', 'classification', 'deepseek'],
    cost: '~12 ₽ / 1000 обращений',
    description:
      'DeepSeek V4-Flash + структурированный output: company_size, budget, fit_score. Готовый промпт.',
    href: '/cookbook/lead-qualification',
  },
  {
    title: 'Support — классификация тикетов и роутинг',
    tags: ['support', 'routing', 'gemini'],
    cost: '~220 ₽ / 1000 тикетов',
    description:
      'Из текста обращения → priority, department, suggested_response. Gemini 3 Flash, 2 секунды на тикет.',
    href: '/cookbook/support-ticket-classifier',
  },
];

export function CookbookTeaser() {
  return (
    <section id="cookbook" className="brikko-section">
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 64px',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Рецепты
        </span>
        <h2 className="brikko-h2">
          <span>Готовые блоки. </span>
          <span className="brikko-h2-italic">Считаем</span> экономику.
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          curl + system prompt + JSON Schema — копируйте в свой код. Цена в рублях
          пересчитана на тысячу запросов.
        </p>
      </header>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
      >
        {RECIPES.map((r) => (
          <Link
            key={r.title}
            href={r.href as Route}
            className="brikko-card-outer"
            style={{ textDecoration: 'none', color: 'inherit' }}
          >
            <div
              className="brikko-card-inner"
              style={{
                padding: '24px 24px 22px',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 220,
              }}
            >
              <div
                style={{
                  display: 'inline-flex',
                  flexWrap: 'wrap',
                  gap: 6,
                  marginBottom: 16,
                }}
              >
                {r.tags.map((t) => (
                  <span
                    key={t}
                    style={{
                      display: 'inline-flex',
                      padding: '3px 8px',
                      border: '1px solid var(--hairline)',
                      borderRadius: 9999,
                      fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                      fontSize: 9,
                      fontWeight: 500,
                      letterSpacing: '0.15em',
                      textTransform: 'uppercase',
                      color: 'var(--fg-muted)',
                    }}
                  >
                    {t}
                  </span>
                ))}
              </div>
              <h3
                style={{
                  fontWeight: 500,
                  fontSize: 16,
                  lineHeight: 1.35,
                  letterSpacing: '-0.01em',
                  color: 'var(--fg-primary)',
                  margin: '0 0 10px',
                }}
              >
                {r.title}
              </h3>
              <p
                style={{
                  fontSize: 14,
                  lineHeight: 1.55,
                  color: 'var(--fg-muted)',
                  margin: 0,
                }}
              >
                {r.description}
              </p>
              <div style={{ flex: 1, minHeight: 12 }} />
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                  paddingTop: 16,
                  borderTop: '1px solid var(--hairline)',
                }}
              >
                <span
                  style={{
                    fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                    fontSize: 13,
                    color: 'var(--fg-primary)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {r.cost}
                </span>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 13,
                    color: 'var(--fg-muted)',
                  }}
                >
                  Открыть
                  <ArrowIcon />
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}

function ArrowIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
