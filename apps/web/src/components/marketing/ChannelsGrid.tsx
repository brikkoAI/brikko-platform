import Link from 'next/link';
import type { Route } from 'next';
import { CodeCopyButton } from './CodeCopyButton';

/**
 * ChannelsGrid — секция «6 способов защитить ваш AI» (PII pivot, 2026-05-14).
 *
 * Grid 3×2 из 6 distribution channels (BRIEF_v2_pivot.md §4). Все каналы
 * ходят на единый backend api.brikko.ru/v1/anonymize — но visitor видит
 * 6 артефактов по своему интерфейсу: Chrome extension, desktop binary,
 * CLI, Claude Code skill, n8n nodes, PyPI library.
 *
 * UX-обоснование:
 *   - Карточная сетка вместо длинного списка: каждый канал = decision point
 *     («это мой случай — копирую команду»), а не fragment списка.
 *   - Install-команда видна без раскрытия: visitor видит и копирует одной
 *     кнопкой, нет лишнего click-through.
 *   - Только Shield (Chrome extension) — disabled state с «Скоро в Web Store»,
 *     остальные 5 — рабочие реестры (npm/PyPI/Docker/GitHub).
 *   - Внешние ссылки (npmjs.com, pypi.org, github.com) — обычный <a>
 *     (typedRoutes не позволит Link на external). /studio — Link, потому
 *     что dedicated marketing page существует.
 *
 * a11y:
 *   - Семантический <article> per card + h3 для названия.
 *   - <code> внутри install-команды — screen reader зачитает как код.
 *   - aria-disabled на Shield CTA (не button, чтобы фокус сохранялся).
 */

type Channel = {
  iconKey: 'shield' | 'studio' | 'cli' | 'skill' | 'n8n' | 'presidio';
  name: string;
  audience: string;
  install: string;
  href: string;
  hrefIsInternal?: boolean;
  ctaLabel: string;
  disabled?: boolean;
  disabledHint?: string;
};

const CHANNELS: Channel[] = [
  {
    iconKey: 'shield',
    name: 'Brikko Shield',
    audience: 'Chrome extension для физлиц и HR',
    install: 'Скоро в Chrome Web Store',
    href: '#channels',
    ctaLabel: 'Wait-list',
    disabled: true,
    disabledHint: 'Готовится к публикации в Chrome Web Store',
  },
  {
    iconKey: 'studio',
    name: 'Brikko Studio',
    audience: 'Desktop AI agent с MCP для Bitrix24 / 1С',
    install: 'curl install.brikko.ru/studio.sh | bash',
    href: '/studio',
    hrefIsInternal: true,
    ctaLabel: 'Подробнее о Studio',
  },
  {
    iconKey: 'cli',
    name: 'Brikko CLI',
    audience: 'Управление Studio из терминала',
    install: 'npm install -g brikko-cli',
    href: 'https://www.npmjs.com/package/brikko-cli',
    ctaLabel: 'npmjs.com/brikko-cli',
  },
  {
    iconKey: 'skill',
    name: 'PII Skill',
    audience: 'Для Claude Code, Cursor, Codex агентов',
    install: 'git clone github.com/brikkoAI/brikko-pii-skill ~/.claude/skills/',
    href: 'https://github.com/brikkoAI/brikko-pii-skill',
    ctaLabel: 'GitHub репозиторий',
  },
  {
    iconKey: 'n8n',
    name: 'n8n nodes',
    audience: 'Маскинг в n8n workflows',
    install: 'npm install n8n-nodes-brikko',
    href: 'https://www.npmjs.com/package/n8n-nodes-brikko',
    ctaLabel: 'npmjs.com/n8n-nodes-brikko',
  },
  {
    iconKey: 'presidio',
    name: 'Presidio recognizers',
    audience: 'Российские entities для Microsoft Presidio',
    install: 'pip install presidio-ru-recognizers',
    href: 'https://pypi.org/project/presidio-ru-recognizers/',
    ctaLabel: 'pypi.org/presidio-ru-recognizers',
  },
];

export function ChannelsGrid() {
  return (
    <section id="channels" className="brikko-section">
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 56px',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          6 каналов, одна инфраструктура
        </span>
        <h2 className="brikko-h2">
          <span>6 способов защитить </span>
          <span className="brikko-h2-italic">ваш AI.</span>
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          Все каналы ходят на единый backend{' '}
          <code
            style={{
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: '0.9em',
              padding: '2px 6px',
              background: 'var(--bg-tier-2)',
              borderRadius: 6,
              border: '1px solid var(--hairline)',
            }}
          >
            api.brikko.ru/v1/anonymize
          </code>
          . Выберите интерфейс под вашу задачу — установка занимает меньше минуты.
        </p>
      </header>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
        className="brikko-channels-grid"
      >
        {CHANNELS.map((channel) => (
          <ChannelCard key={channel.name} channel={channel} />
        ))}
      </div>
    </section>
  );
}

function ChannelCard({ channel }: { channel: Channel }) {
  return (
    <article className="brikko-card-outer" style={{ display: 'flex' }}>
      <div
        className="brikko-card-inner"
        style={{
          padding: '28px 24px 24px',
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          gap: 16,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span
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
              flexShrink: 0,
            }}
          >
            <ChannelGlyph iconKey={channel.iconKey} />
          </span>
          <h3
            style={{
              fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
              fontWeight: 400,
              fontSize: 22,
              lineHeight: 1.2,
              letterSpacing: '-0.015em',
              color: 'var(--fg-primary)',
              margin: 0,
            }}
          >
            {channel.name}
          </h3>
        </div>

        <p
          style={{
            fontSize: 14,
            lineHeight: 1.5,
            color: 'var(--fg-muted)',
            margin: 0,
          }}
        >
          {channel.audience}
        </p>

        {channel.disabled ? (
          <div
            style={{
              padding: '14px 16px',
              background: 'var(--bg-tier-2)',
              border: '1px solid var(--hairline)',
              borderRadius: 12,
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 12,
              color: 'var(--fg-faint)',
              letterSpacing: '0.02em',
            }}
          >
            {channel.install}
          </div>
        ) : (
          <CodeCopyButton command={channel.install} />
        )}

        <div style={{ flex: 1, minHeight: 4 }} />

        {channel.disabled ? (
          <span
            aria-disabled="true"
            className="brikko-btn brikko-btn-secondary"
            style={{
              justifyContent: 'center',
              width: '100%',
              opacity: 0.55,
              cursor: 'not-allowed',
            }}
            title={channel.disabledHint}
          >
            {channel.ctaLabel}
          </span>
        ) : channel.hrefIsInternal ? (
          <Link
            href={channel.href as Route}
            className="brikko-btn brikko-btn-secondary"
            style={{ justifyContent: 'center', width: '100%' }}
          >
            {channel.ctaLabel}
          </Link>
        ) : (
          <a
            href={channel.href}
            target="_blank"
            rel="noopener noreferrer"
            className="brikko-btn brikko-btn-secondary"
            style={{ justifyContent: 'center', width: '100%' }}
          >
            {channel.ctaLabel}
          </a>
        )}
      </div>
    </article>
  );
}

function ChannelGlyph({ iconKey }: { iconKey: Channel['iconKey'] }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (iconKey === 'shield') {
    return (
      <svg {...common}>
        <path d="M12 3l8 3v6c0 4.5-3.4 8.4-8 9-4.6-.6-8-4.5-8-9V6l8-3z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    );
  }
  if (iconKey === 'studio') {
    return (
      <svg {...common}>
        <rect x="3" y="4" width="18" height="13" rx="2" />
        <path d="M8 21h8" />
        <path d="M12 17v4" />
        <circle cx="12" cy="10.5" r="2" />
      </svg>
    );
  }
  if (iconKey === 'cli') {
    return (
      <svg {...common}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M7 9l3 3-3 3" />
        <path d="M13 15h4" />
      </svg>
    );
  }
  if (iconKey === 'skill') {
    return (
      <svg {...common}>
        <path d="M12 3l2.5 5 5.5.8-4 3.9.9 5.5L12 15.5 7.1 18.2 8 12.7 4 8.8l5.5-.8L12 3z" />
      </svg>
    );
  }
  if (iconKey === 'n8n') {
    return (
      <svg {...common}>
        <circle cx="6" cy="6" r="2.2" />
        <circle cx="18" cy="6" r="2.2" />
        <circle cx="12" cy="12" r="2.2" />
        <circle cx="6" cy="18" r="2.2" />
        <circle cx="18" cy="18" r="2.2" />
        <path d="M7.6 7.6L10.4 10.4" />
        <path d="M16.4 7.6L13.6 10.4" />
        <path d="M7.6 16.4L10.4 13.6" />
        <path d="M16.4 16.4L13.6 13.6" />
      </svg>
    );
  }
  // presidio
  return (
    <svg {...common}>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h10" />
      <circle cx="18" cy="17" r="2.5" />
    </svg>
  );
}
