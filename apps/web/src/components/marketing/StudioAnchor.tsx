import Link from 'next/link';
import type { Route } from 'next';
import { CodeCopyButton } from './CodeCopyButton';

/**
 * StudioAnchor — Editorial Split-блок для главной (Sprint 13.7, 2026-05-05).
 *
 * Анонс Brikko Studio v0.3.0: левая колонка — массивная типографика +
 * curl-инсталл + ссылка на /studio; правая — ASCII-схема архитектуры
 * (Browser → Studio Core + Anonymizer → api.brikko.ru).
 *
 * UX-обоснование:
 *   - Studio это side-product (desktop-агент с reversible PII masking),
 *     но он отличает Brikko от чистых API-prodxy. На главной — anchor сразу
 *     после Hero, до features/pricing, чтобы visitor с GitHub-traffic'ом
 *     сразу видел self-hosted-опцию.
 *   - Editorial Split (как Hero): H2 + лид + curl-pill слева, диаграмма
 *     справа. Та же анатомия — пользователь не учит новый паттерн.
 *   - ASCII-диаграмма вместо PNG: 0 КБ overhead, theme-aware (через CSS-vars),
 *     accessible (screen reader читает структуру).
 */

const INSTALL_COMMAND = 'npm install -g brikko-cli && brikko init';

const DIAGRAM = `┌─ Browser ──────┐
│ Chat UI :3737  │
└────┬───────────┘
     ↓ /api/auth/*
┌─ Studio Core ──┐    ┌─ Anonymizer ──┐
│ + privacy-     │ ←→ │ FastAPI :8403 │
│   plugin       │    │ + Natasha NER │
└────┬───────────┘    └───────────────┘
     ↓ HTTPS (masked)
   api.brikko.ru → LLM
   (no real PII ever leaves)`;

export function StudioAnchor() {
  return (
    <section
      id="studio"
      style={{
        position: 'relative',
        padding: '80px 6vw',
        zIndex: 2,
      }}
    >
      <div
        style={{
          maxWidth: 1400,
          width: '100%',
          margin: '0 auto',
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
          gap: 64,
          alignItems: 'center',
        }}
        className="hero-split"
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
          <span
            className="brikko-eyebrow"
            style={{
              background: 'var(--accent-1-soft)',
              border: '1px solid var(--hairline)',
              padding: '6px 14px',
              borderRadius: 9999,
            }}
          >
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Новое · v0.3.0
          </span>

          <h2
            className="brikko-h2"
            style={{ marginTop: 24, fontSize: 'clamp(32px, 4vw, 56px)', maxWidth: '14ch' }}
          >
            <span>Brikko Studio </span>
            <span className="brikko-h2-italic">теперь доступен</span>
          </h2>

          <p
            className="brikko-lede"
            style={{
              marginTop: 16,
              marginBottom: 24,
              maxWidth: '46ch',
            }}
          >
            Десктопный AI-агент с reversible PII-маскингом. Self-hosted,
            открытый код, ставится одной командой.
          </p>

          <div style={{ width: '100%', maxWidth: 560, marginBottom: 16 }}>
            <CodeCopyButton command={INSTALL_COMMAND} />
          </div>

          <Link
            href={'/studio' as Route}
            style={{
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 13,
              color: 'var(--fg-primary)',
              textDecoration: 'underline',
              textDecorationColor: 'var(--hairline-hi)',
              textUnderlineOffset: 4,
              transition: 'text-decoration-color 240ms var(--ease-in-out-quart)',
            }}
          >
            Узнать больше →
          </Link>
        </div>

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
                studio.brikko
              </span>
            </div>
            <pre
              style={{
                margin: 0,
                padding: '20px 22px 24px',
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                fontSize: 12.5,
                lineHeight: 1.55,
                color: 'var(--code-fg)',
                opacity: 0.86,
                whiteSpace: 'pre',
                overflowX: 'auto',
              }}
            >
{DIAGRAM}
            </pre>
          </div>
        </div>
      </div>
    </section>
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
