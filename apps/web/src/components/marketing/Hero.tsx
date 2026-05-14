import Link from 'next/link';
import type { Route } from 'next';

/**
 * Hero — Cream Studio v6 + PII pivot (2026-05-14).
 *
 * Editorial Split: H1 слева (Source Serif 4), code-window справа.
 * Дизайн (Cream Studio v6, одобрен CEO 2026-05-02) НЕ меняем — меняем
 * только копию под новое позиционирование «AI Privacy Ecosystem»
 * (см. BRIEF_v2_pivot.md, 2026-05-14).
 *
 * UX:
 *   - H1 в 3 строки: «AI без / утечки / 152-ФЗ.» Цифра «152» italic как
 *     визуальный акцент (паттерн совпадает с прошлым Hero — пользователь
 *     не учит новый ритм).
 *   - Subhead: какие данные маскируем, в какие LLM, как локально (Natasha).
 *   - Primary CTA → /studio (рабочий маршрут, brikko-studio артефакт).
 *     Вторичный — Shield wait-list mailto (Chrome Web Store ещё не залит).
 *   - Welcome-бонус 200 ₽ убран — новая модель Free tier 100 запросов/день.
 *   - Code-window: пример curl к api.brikko.ru/v1/anonymize (а не /v1/messages),
 *     с показом ответа с placeholder'ом — это объясняет ценность за 5 секунд.
 */

const HERO_LINES: { words: { text: string; italic?: boolean }[] }[] = [
  { words: [{ text: 'AI' }, { text: 'без' }] },
  { words: [{ text: 'утечки', italic: true }] },
  { words: [{ text: '152-ФЗ.', italic: true }] },
];

export function Hero() {
  return (
    <section
      id="hero"
      style={{
        position: 'relative',
        minHeight: '100dvh',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '160px 6vw 120px',
        zIndex: 2,
      }}
    >
      <div
        style={{
          position: 'relative',
          zIndex: 2,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
          gap: 64,
          alignItems: 'center',
          maxWidth: 1400,
          width: '100%',
          margin: '0 auto',
        }}
        className="hero-split"
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
          <span className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Brikko Privacy
          </span>

          <h1 className="brikko-h1" style={{ fontSize: 'clamp(40px, 5vw, 80px)', maxWidth: '14ch' }}>
            {HERO_LINES.map((line, li) => (
              <span key={li} style={{ display: 'block' }}>
                {line.words.map((w, wi) => (
                  <span
                    key={wi}
                    style={{ fontStyle: w.italic ? 'italic' : 'normal' }}
                  >
                    {w.text}
                    {wi < line.words.length - 1 ? ' ' : ''}
                  </span>
                ))}
              </span>
            ))}
          </h1>

          <p className="brikko-lede">
            Маскируем ФИО, ИНН, СНИЛС, паспорт РФ перед отправкой в ChatGPT,
            Claude, Gemini, GigaChat и YandexGPT. Локальное обезличивание
            через Natasha NER. 6 способов установить за минуту.
          </p>

          <div
            style={{
              position: 'relative',
              zIndex: 2,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 12,
              alignItems: 'center',
            }}
          >
            <a
              href="#channels"
              className="brikko-btn brikko-btn-primary"
            >
              <span>Установить расширение</span>
              <span className="brikko-btn-icon-wrap" aria-hidden="true">
                <ArrowIcon />
              </span>
            </a>
            <Link href={'/studio' as Route} className="brikko-btn brikko-btn-secondary">
              Запустить Studio (1 команда)
            </Link>
          </div>

          <p style={{ marginTop: 16, color: 'var(--fg-faint)', fontSize: 13 }}>
            Free tier: 100 запросов в день. Карта не нужна, регистрация
            необязательна для open-source каналов.
          </p>
        </div>

        <HeroCodeWindow />
      </div>
    </section>
  );
}

function HeroCodeWindow() {
  return (
    <div
      aria-hidden="true"
      style={{
        position: 'relative',
        width: '100%',
        background: 'var(--bg-tier-2)',
        borderRadius: 32,
        padding: 6,
        border: '1px solid var(--hairline)',
        boxShadow: '0 32px 64px -32px rgba(28, 25, 23, 0.18)',
      }}
    >
      <div
        style={{
          position: 'relative',
          background: 'var(--code-bg)',
          borderRadius: 26,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 7,
            padding: '14px 18px',
            borderBottom: '1px solid rgba(245, 245, 244, 0.08)',
          }}
        >
          <span style={dotStyle(0.18)} />
          <span style={dotStyle(0.13)} />
          <span style={dotStyle(0.09)} />
          <span
            style={{
              marginLeft: 'auto',
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 11,
              color: 'var(--code-accent)',
              letterSpacing: '0.04em',
            }}
          >
            anonymize.sh
          </span>
        </div>
        <pre
          style={{
            margin: 0,
            padding: '18px 20px 22px',
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 13,
            lineHeight: 1.65,
            color: 'var(--code-fg)',
            opacity: 0.78,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
{`curl https://api.brikko.ru/v1/anonymize \\
  -H "Content-Type: application/json" \\
  -d '{
    "text": "Иванов Иван Иванович, ИНН 7707083893,
             паспорт 4509 123456"
  }'`}
        </pre>
        <pre
          style={{
            margin: 0,
            padding: '0 20px 22px',
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 13,
            lineHeight: 1.65,
            color: 'var(--code-accent)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
{`{
  "anonymized": "[PERSON_1], ИНН [INN_1],
                 паспорт [PASSPORT_1]",
  "entities": 3
}`}
        </pre>
      </div>
    </div>
  );
}

function dotStyle(opacity: number): React.CSSProperties {
  return {
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: `rgba(245, 245, 244, ${opacity})`,
  };
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
