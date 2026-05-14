/**
 * ObservabilityShowcase — Cream Studio v6 (Sprint 13.8 landing refresh,
 * 2026-05-09).
 *
 * Третья крупная секция лендинга после Hero + Proof + Models + Roadmap +
 * Features. Показывает три observability-страницы клиента в browser-chrome
 * рамках:
 *
 *   ┌─────────────────────────────┐ ┌──────────────────┐
 *   │ EYEBROW: BrikkoLens         │ │ /app/traces      │
 *   │ H2 + tagline                │ ├──────────────────┤
 *   │                             │ │ /app/status      │
 *   │ ┌────────────────────────┐  │ └──────────────────┘
 *   │ │ /app/analytics FEATURED│  │
 *   │ └────────────────────────┘  │
 *   └─────────────────────────────┘
 *
 * UX-обоснование:
 *   - Asymmetric grid (8+4) ставит analytics-FEATURED крупнее остальных:
 *     это «мясо» observability-предложения (KPI + графики + breakdown). Traces
 *     и status — secondary доказательства, доступны как teaser-карточки.
 *   - Browser-chrome рамка (URL-bar + 3 dots) даёт визуальное «это реальный
 *     UI», не decorative-skeuomorphism.  На mobile рамка убрана — занимает
 *     место.
 *   - Карточки traces / status кликабельны (→ /demo/{traces,status}). Featured
 *     analytics ссылается на /demo/analytics через явную CTA-ссылку под
 *     рамкой — это чище чем делать всю огромную карточку single anchor.
 *   - Цветовой бюджет (Cream Studio v6): один зелёный пиксель в светофоре
 *     status'а + один accent-1 пятно в bar-chart analytics. Остальное
 *     grayscale — без gradient'ов, без cheap shadows.
 */

import Link from 'next/link';
import type { Route } from 'next';
import { ArrowUpRight } from 'lucide-react';
import { AnalyticsMockView, StatusMockView, TracesMockView } from './observability-mocks';

const FEATURED_HREF = '/demo/analytics' as Route;
const TRACES_HREF = '/demo/traces' as Route;
const STATUS_HREF = '/demo/status' as Route;

export function ObservabilityShowcase() {
  return (
    <section id="observability" className="brikko-section brikko-observability">
      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
          display: 'grid',
          gridTemplateColumns: '8fr 4fr',
          gap: 28,
          alignItems: 'start',
        }}
        className="brikko-observability-grid"
      >
        {/* LEFT: header + featured analytics */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
          <header style={{ display: 'flex', flexDirection: 'column' }}>
            <span
              className="brikko-eyebrow"
              style={{ color: 'var(--accent-1)', borderColor: 'var(--hairline-hi)' }}
            >
              <span
                className="brikko-eyebrow-dot"
                aria-hidden="true"
                style={{ background: 'var(--accent-1)' }}
              />
              BrikkoLens
            </span>
            <h2 className="brikko-h2 brikko-observability-h2">
              Каждый токен —{' '}
              <span className="brikko-h2-italic">под наблюдением.</span>
            </h2>
            <p
              className="brikko-lede"
              style={{ marginBottom: 8, maxWidth: '52ch' }}
            >
              Трейсы, KPI и здоровье провайдеров — встроены в кабинет. Без
              Datadog, без Grafana, без отдельного counter&rsquo;а в Prometheus.
            </p>
          </header>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <BrowserChrome url="brikko.ru/app/analytics" featured>
              <AnalyticsMockView />
            </BrowserChrome>
            <Link href={FEATURED_HREF} className="brikko-observability-cta">
              <span>Открыть demo-аналитику</span>
              <ArrowRight />
            </Link>
          </div>
        </div>

        {/* RIGHT: traces + status, clickable cards */}
        <div className="brikko-observability-side">
          <Link
            href={TRACES_HREF}
            className="brikko-observability-card"
            aria-label="Открыть demo-страницу /app/traces"
          >
            <BrowserChrome url="brikko.ru/app/traces" minHeight={280}>
              <TracesMockView compact />
            </BrowserChrome>
            <span className="brikko-observability-arrow" aria-hidden="true">
              <ArrowUpRight className="h-3.5 w-3.5" />
            </span>
          </Link>

          <Link
            href={STATUS_HREF}
            className="brikko-observability-card"
            aria-label="Открыть demo-страницу /app/status"
          >
            <BrowserChrome url="brikko.ru/app/status" minHeight={280}>
              <StatusMockView compact />
            </BrowserChrome>
            <span className="brikko-observability-arrow" aria-hidden="true">
              <ArrowUpRight className="h-3.5 w-3.5" />
            </span>
          </Link>
        </div>
      </div>

      <style>{`
        /* CSS-grid 1fr не сжимается ниже content-min-size без min-width:0.
           Без этого фикса жёстко-фиксированные колонки внутри TracesMockView
           распирали правый контейнер до 600+px, ломая 8fr/4fr пропорции. */
        .brikko-observability-grid > * {
          min-width: 0;
        }
        .brikko-observability-h2 {
          margin-bottom: 16px;
        }
        .brikko-observability-cta {
          align-self: flex-start;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 10px 18px;
          border-radius: 9999px;
          background: transparent;
          border: 1px solid var(--hairline-hi);
          color: var(--fg-primary);
          font-size: 14px;
          font-weight: 500;
          text-decoration: none;
          transition:
            border-color 240ms var(--ease-out-quint),
            background 240ms var(--ease-out-quint),
            transform 240ms var(--ease-out-quint);
        }
        .brikko-observability-cta:hover {
          border-color: var(--fg-primary);
          background: var(--accent-1-soft);
          transform: translateY(-1px);
        }
        .brikko-observability-side {
          display: flex;
          flex-direction: column;
          gap: 20px;
          position: sticky;
          top: 100px;
        }
        .brikko-observability-card {
          position: relative;
          display: block;
          text-decoration: none;
          color: inherit;
          border-radius: 18px;
          transition: transform 240ms var(--ease-out-quint);
        }
        .brikko-observability-card:hover {
          transform: translateY(-2px);
        }
        .brikko-observability-card:hover .brikko-observability-frame {
          border-color: var(--hairline-hi);
        }
        .brikko-observability-card:hover .brikko-observability-arrow {
          opacity: 1;
          transform: translate(-4px, 4px);
        }
        .brikko-observability-arrow {
          position: absolute;
          top: 14px;
          right: 14px;
          width: 28px;
          height: 28px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 9999px;
          background: var(--bg-base);
          border: 1px solid var(--hairline);
          color: var(--fg-primary);
          opacity: 0;
          transition:
            opacity 200ms var(--ease-out-quint),
            transform 200ms var(--ease-out-quint);
          pointer-events: none;
        }

        @media (max-width: 1023px) {
          .brikko-observability-grid {
            grid-template-columns: 1fr !important;
          }
          .brikko-observability-side {
            position: static !important;
          }
        }
        @media (max-width: 640px) {
          .brikko-observability-h2 {
            font-size: 32px !important;
            line-height: 1.1;
          }
          .brikko-observability-frame {
            border-radius: 16px !important;
            border: 1px solid var(--hairline) !important;
            padding: 0 !important;
          }
          .brikko-observability-frame .brikko-observability-urlbar {
            display: none !important;
          }
          .brikko-observability-card:hover {
            transform: none;
            background: var(--accent-1-soft);
            border-radius: 16px;
          }
          .brikko-observability-card:hover .brikko-observability-arrow {
            opacity: 0;
          }
        }
      `}</style>
    </section>
  );
}

function BrowserChrome({
  url,
  children,
  featured = false,
  minHeight,
}: {
  url: string;
  children: React.ReactNode;
  featured?: boolean;
  minHeight?: number;
}) {
  return (
    <div
      className="brikko-observability-frame"
      style={{
        position: 'relative',
        background: 'var(--bg-elevated)',
        borderRadius: 24,
        padding: 6,
        border: '1px solid var(--hairline)',
        boxShadow: featured ? '0 32px 64px -32px rgba(28, 25, 23, 0.18)' : undefined,
        overflow: 'hidden',
        transition: 'border-color 240ms var(--ease-out-quint)',
      }}
    >
      <div
        style={{
          background: 'var(--bg-base)',
          borderRadius: 18,
          overflow: 'hidden',
          minHeight,
        }}
      >
        <div
          className="brikko-observability-urlbar"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '10px 14px',
            borderBottom: '1px solid var(--hairline)',
          }}
        >
          <div style={{ display: 'flex', gap: 6 }}>
            <span style={dotStyle()} />
            <span style={dotStyle()} />
            <span style={dotStyle()} />
          </div>
          <div
            style={{
              flex: 1,
              padding: '4px 10px',
              borderRadius: 999,
              background: 'var(--bg-elevated)',
              color: 'var(--fg-faint)',
              fontFamily: 'var(--font-mono), ui-monospace, monospace',
              fontSize: 11,
              border: '1px solid var(--hairline)',
              textAlign: 'center',
              maxWidth: 360,
              margin: '0 auto',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {url}
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function dotStyle(): React.CSSProperties {
  return {
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: 'var(--hairline-hi)',
  };
}

function ArrowRight() {
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
      aria-hidden="true"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
