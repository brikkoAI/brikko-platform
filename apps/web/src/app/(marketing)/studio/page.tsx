import Link from 'next/link';
import type { Route } from 'next';
import { CodeCopyButton } from '@/components/marketing/CodeCopyButton';

export const metadata = {
  title: 'Brikko Studio',
  description:
    'Десктопный AI-агент с reversible PII masking. Self-hosted, открытый код, ставится одной командой.',
};

const INSTALL_COMMAND = 'npm install -g brikko-cli && brikko init';
const FALLBACK_COMMAND = 'curl -sSL https://install.brikko.ru/studio.sh | bash';

const PILLARS: { title: string; body: string }[] = [
  {
    title: 'Reversible PII masking',
    body:
      'Studio локально маскирует ФИО, телефоны, email и другие персданные перед отправкой запроса в LLM. Ответ модели разворачивается обратно — пользователь видит исходные имена, провайдер видит токены [PERSON_1], [EMAIL_2].',
  },
  {
    title: 'Self-hosted, без vendor lock-in',
    body:
      'Studio Core + Anonymizer ставятся локально (Docker / native binary). Brikko Cloud — опциональный fallback для billing, но Studio работает и off-line с собственными API-ключами OpenAI / Anthropic / YandexGPT.',
  },
  {
    title: 'Открытый код',
    body:
      'Studio Core, privacy-plugin и Anonymizer лежат на github.com/brikkoAI. Pull request приветствуется, fork-ом легально пользоваться. MIT-лицензия для core, AGPL для Anonymizer.',
  },
];

/**
 * /studio — landing для desktop-агента (Sprint 13.7, 2026-05-05).
 *
 * Минимальная версия пока: hero (H1 + CTA + curl), 3 pillar-блока,
 * troubleshooting CTA. Будет расширена в Sprint 14 (скриншоты,
 * сравнение с ChatGPT Desktop, видео-демо).
 */
export default function StudioPage() {
  return (
    <>
      <section
        style={{
          position: 'relative',
          padding: '180px 6vw 80px',
          maxWidth: 1400,
          margin: '0 auto',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Brikko Studio · v0.3.0
        </span>
        <h1
          className="brikko-h1"
          style={{ fontSize: 'clamp(40px, 5vw, 80px)', maxWidth: '14ch' }}
        >
          <span>Локальный </span>
          <span className="brikko-h2-italic">AI-агент</span>
          <span> с защитой PII.</span>
        </h1>
        <p className="brikko-lede" style={{ marginBottom: 24 }}>
          Десктопный клиент с reversible PII-маскингом. Ваши данные не покидают
          машину в чистом виде — провайдер видит только токены вроде [PERSON_1].
          Self-hosted, открытый код, ставится одной командой.
        </p>

        <div style={{ maxWidth: 640, marginBottom: 12 }}>
          <CodeCopyButton command={INSTALL_COMMAND} />
        </div>
        <p
          style={{
            fontSize: 12.5,
            color: 'var(--fg-muted)',
            marginBottom: 24,
            maxWidth: 640,
          }}
        >
          Нет Node.js?{' '}
          <a
            href="#install-fallback"
            style={{ color: 'inherit', textDecoration: 'underline' }}
          >
            Альтернативная установка через curl &amp; bash →
          </a>
        </p>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <a
            href="https://github.com/brikkoAI/brikko-studio"
            target="_blank"
            rel="noopener noreferrer"
            className="brikko-btn brikko-btn-secondary"
          >
            GitHub репозиторий
          </a>
          <Link href={'/docs/studio' as Route} className="brikko-btn brikko-btn-secondary">
            Документация
          </Link>
        </div>
      </section>

      <div className="brikko-divider" aria-hidden="true" />

      <section className="brikko-section" id="pillars">
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
            Что внутри
          </span>
          <h2 className="brikko-h2">
            <span>Три </span>
            <span className="brikko-h2-italic">столпа</span>
            <span> Studio.</span>
          </h2>
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
          {PILLARS.map((p, i) => (
            <article key={p.title} className="brikko-card-outer">
              <div className="brikko-card-inner" style={{ padding: '28px 24px', minHeight: 240 }}>
                <span
                  style={{
                    fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                    fontSize: 12,
                    color: 'var(--fg-muted)',
                    letterSpacing: '0.08em',
                  }}
                >
                  0{i + 1}
                </span>
                <h3
                  style={{
                    marginTop: 12,
                    fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                    fontWeight: 400,
                    fontSize: 24,
                    letterSpacing: '-0.015em',
                    color: 'var(--fg-primary)',
                  }}
                >
                  {p.title}
                </h3>
                <p
                  style={{
                    marginTop: 12,
                    fontSize: 14.5,
                    lineHeight: 1.6,
                    color: 'var(--fg-muted)',
                  }}
                >
                  {p.body}
                </p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <div className="brikko-divider" aria-hidden="true" />

      <section className="brikko-section" id="install-fallback">
        <header
          style={{
            maxWidth: 1400,
            margin: '0 auto 24px',
            padding: '0 6vw',
            position: 'relative',
            zIndex: 2,
          }}
        >
          <span className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Альтернативная установка
          </span>
          <h2 className="brikko-h2" style={{ fontSize: 'clamp(28px, 3.2vw, 40px)' }}>
            <span>Нет </span>
            <span className="brikko-h2-italic">Node.js</span>
            <span>? Используйте bash-installer.</span>
          </h2>
          <p
            className="brikko-lede"
            style={{
              marginTop: 16,
              maxWidth: '60ch',
              fontSize: 16,
            }}
          >
            Старый installer на bash работает на macOS / Linux / WSL и не требует
            ничего кроме Docker. Скачивает docker-compose.yml через JSDelivr CDN
            (стабильно в РФ без VPN).
          </p>
        </header>
        <div style={{ maxWidth: 800, margin: '0 auto', padding: '0 6vw' }}>
          <CodeCopyButton command={FALLBACK_COMMAND} />
        </div>
      </section>
    </>
  );
}
