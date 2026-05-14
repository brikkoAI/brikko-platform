import { Check } from 'lucide-react';

/**
 * RoadmapSection — «Что в работе».
 *
 * Cream Studio v6 (Sprint 13.7, P1 polish, 2026-05-06).
 *
 * UX-обоснование: между «вот что есть» (ModelsShowcase) и «вот почему лучше»
 * (Features) ставим открытый план развития. Снижает FOMO у скептиков
 * («а оно вообще живое?»), даёт visitor'у reason to come back, и сигналит
 * что Brikko — продукт, а не just-API-prox.
 *
 * Visual: 4 карточки в grid-3 (на десктопе) / grid-1 (mobile). Status-pill
 * сверху каждой карточки: Live (галка) / 2 нед / 6 нед — компактный таймлайн
 * без даты, чтобы не привязываться к конкретному числу. Структура копирует
 * Features.tsx — пользователь не учит новый паттерн.
 */

interface RoadmapItem {
  title: string;
  description: string;
  status: 'live' | 'beta' | 'in-progress';
  /** Текст под status pill: «Live» / «Beta» / «2 недели». */
  statusLabel: string;
}

const ROADMAP: readonly RoadmapItem[] = [
  {
    title: 'Privacy Mode v2',
    description:
      'Reversible PII-маскинг pre-LLM: ФИО, email, телефоны, договорные суммы. 152-ФЗ-friendly.',
    status: 'live',
    statusLabel: 'Live',
  },
  {
    title: 'Brikko Studio',
    description:
      'Десктопный AI-агент c локальным анонимизатором. Self-hosted, открытый код, ставится через npm install -g brikko-cli.',
    status: 'live',
    statusLabel: 'Live',
  },
  {
    title: 'Brikko Shield (browser)',
    description:
      'Расширение для Chrome: маскирует PII в Claude в браузере до отправки. MVP уже на GitHub, ChatGPT/Gemini поддержка — в работе.',
    status: 'beta',
    statusLabel: 'Beta',
  },
  {
    title: 'n8n nodes',
    description:
      'Кастомные ноды для n8n: Anonymize, Restore, Chat, DetectPii — drop-in защита PII в существующих workflow.',
    status: 'live',
    statusLabel: 'Live',
  },
  {
    title: 'PII Mask skill',
    description:
      'Skill для Codex / Claude Code / OpenClaw агентов: маскирует ФИО, ИНН, СНИЛС, ОГРН, паспорта, телефоны, банковские счета до отправки в LLM. Установка одной строкой.',
    status: 'live',
    statusLabel: 'Live',
  },
  {
    title: 'LangChain adapter',
    description:
      'ChatBrikko provider + embedding wrapper для LangChain — Brikko как drop-in замена OpenAI/Anthropic providers.',
    status: 'in-progress',
    statusLabel: '2 недели',
  },
];

export function RoadmapSection() {
  return (
    <section
      className="brikko-section"
      aria-labelledby="roadmap-heading"
      style={{ position: 'relative', zIndex: 2 }}
    >
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 56px',
          padding: '0 6vw',
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Roadmap
        </span>
        <h2 id="roadmap-heading" className="brikko-h2">
          <span>Что </span>
          <span className="brikko-h2-italic">в работе</span>
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          Открытый план: что уже доступно и что выйдет ближайшие недели.
        </p>
      </header>

      <ul
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          gap: 16,
          listStyle: 'none',
        }}
      >
        {ROADMAP.map((item) => (
          <li key={item.title} style={{ height: '100%' }}>
            <article className="brikko-card-outer" style={{ height: '100%' }}>
              <div
                className="brikko-card-inner"
                style={{
                  padding: 24,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                  height: '100%',
                }}
              >
                <StatusPill status={item.status} label={item.statusLabel} />
                <h3
                  style={{
                    fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                    fontWeight: 400,
                    fontSize: 22,
                    lineHeight: 1.2,
                    letterSpacing: '-0.01em',
                    color: 'var(--fg-primary)',
                    margin: 0,
                  }}
                >
                  {item.title}
                </h3>
                <p
                  style={{
                    fontSize: 14,
                    lineHeight: 1.55,
                    color: 'var(--fg-muted)',
                    margin: 0,
                  }}
                >
                  {item.description}
                </p>
              </div>
            </article>
          </li>
        ))}
      </ul>
    </section>
  );
}

function StatusPill({
  status,
  label,
}: {
  status: 'live' | 'beta' | 'in-progress';
  label: string;
}) {
  const isLive = status === 'live';
  const isBeta = status === 'beta';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 9999,
        border: `1px solid ${isLive || isBeta ? 'var(--accent-1)' : 'var(--hairline)'}`,
        background: isLive ? 'var(--accent-1-soft)' : 'transparent',
        fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
        fontSize: 11,
        fontWeight: 500,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: 'var(--fg-primary)',
        alignSelf: 'flex-start',
      }}
    >
      {isLive ? (
        <Check
          className="h-3 w-3"
          strokeWidth={2}
          aria-hidden="true"
        />
      ) : (
        <span
          aria-hidden="true"
          style={{
            width: 6,
            height: 6,
            borderRadius: 9999,
            background: isBeta ? 'var(--accent-1)' : 'var(--fg-muted)',
          }}
        />
      )}
      {label}
    </span>
  );
}
