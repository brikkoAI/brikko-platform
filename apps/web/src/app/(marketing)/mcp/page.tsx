import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import {
  Wallet,
  BarChart3,
  Cpu,
  LayoutList,
  Bug,
  BookOpen,
  Plug,
  KeyRound,
  Terminal,
  Sparkles,
} from 'lucide-react';
import { McpPromptCopyBlock } from '@/components/marketing/McpPromptCopyBlock';
import { BRAND } from '@/lib/brand';

/**
 * /mcp — distribution-лендинг для Brikko-MCP onboarding'а (MCP S4, 2026-05-12).
 *
 * Цель страницы: разработчик читает 30 секунд → понимает, что одной фразой
 * Claude/Cursor подключается к Brikko через MCP-протокол и сам показывает
 * баланс, последние расходы, рекомендует модель → копирует промпт →
 * возвращается через час с реальным использованием.
 *
 * Структура (по спецификации CEO 2026-05-12):
 *   1. Hero — H1 «Подключи Claude/Cursor одной фразой» + copy-paste промпт + 2 CTA.
 *   2. How it works — 3 шага визуально.
 *   3. What MCP unlocks — сетка из 7 tool'ов с примерами prompt'ов.
 *   4. Security note — мелким шрифтом про read-only / отдельный токен / audit log.
 *   5. Footer CTA — повтор кнопок «Создать токен» / «Helper на GitHub».
 *
 * UX-обоснование:
 *   - Промпт-блок above the fold — единственный «conversion-point», вокруг
 *     которого вся страница. Поэтому он визуально доминирует (большой
 *     code-window, кнопка «Скопировать» в стандартном месте).
 *   - 7 tool'ов вынесены в карточки с примерами prompt'ов — чтобы разработчик
 *     видел *как именно* он будет общаться с Claude после подключения,
 *     а не абстрактные «JSON-схемы tool'ов».
 *   - Security-note последней секцией (не первой) — для разработчика баланс
 *     важнее, чем «у нас всё безопасно»; страх он развеет уже после интереса.
 *
 * Текущее состояние MCP (S1+S2+S3 на проде):
 *   - 7 tool'ов: read_account, read_usage, recommend_model, list_models,
 *     get_recent_traces, list_cookbook_recipes, list_integrations.
 *   - Endpoint: POST https://api.brikko.ru/mcp.
 *   - Токены: mcp-brk-* (отдельные от sk-brk-*), создаются в /app/keys → tab «MCP keys».
 *   - Helper-repo: https://github.com/brikkoAI/brikko-helper.
 *
 * Server-component, никаких клиентских интерактивов кроме <McpPromptCopyBlock>.
 */

const HELPER_REPO_URL = 'https://github.com/brikkoAI/brikko-helper';

// Точный промпт согласован с CEO 2026-05-12. Не редактировать без согласования —
// после публикации он будет рекламироваться (TG-пост, потом Habr/VC).
const ONBOARDING_PROMPT = `Hi. Clone ${HELPER_REPO_URL}, ask me for my BRIKKO_MCP_TOKEN (I'll grab it from https://app.${BRAND.domain}/app/keys → MCP keys → Create new token), write it into a .env file in this directory using \`python -m brikko_helper init\`, then connect to the MCP endpoint at https://${BRAND.apiDomain}/mcp using the standard MCP transport. Once connected, call read_account and read_usage, show me my balance and last week's spend, then suggest a model for whatever I want to build next.`;

export const metadata: Metadata = {
  title: 'MCP · Model Context Protocol · Brikko',
  description:
    'Подключи Claude или Cursor к Brikko одной фразой. Через MCP агент сам видит твой баланс, расходы, рекомендует модель и подсказывает интеграции — без копирования API-ключей.',
  alternates: { canonical: '/mcp' },
  openGraph: {
    title: 'MCP · Подключи Claude/Cursor к Brikko одной фразой',
    description:
      'Скопируй промпт — Claude клонирует helper, попросит MCP-токен и подключится. Через минуту он знает твой баланс, лимиты и какую модель использовать.',
    url: `https://${BRAND.domain}/mcp`,
    type: 'website',
  },
};

interface ToolCard {
  name: string;
  blurb: string;
  example: string;
  icon: typeof Wallet;
}

const TOOLS: ToolCard[] = [
  {
    name: 'read_account',
    blurb: 'Баланс, активные ключи, тариф.',
    example: '«Какой у меня баланс на Brikko?»',
    icon: Wallet,
  },
  {
    name: 'read_usage',
    blurb: 'Расходы по дням и по моделям за последние 30 дней.',
    example: '«Сколько я потратил за неделю? Какие модели чаще всего?»',
    icon: BarChart3,
  },
  {
    name: 'recommend_model',
    blurb: 'Подбор модели по бюджету, контексту и качеству.',
    example: '«Какую модель взять для классификации тикетов с бюджетом 0.01 ₽ за запрос?»',
    icon: Cpu,
  },
  {
    name: 'list_models',
    blurb: 'Каталог из 38 моделей с фильтрами по цене и контексту.',
    example: '«Покажи самые дешёвые модели с context window больше 100k.»',
    icon: LayoutList,
  },
  {
    name: 'get_recent_traces',
    blurb: 'Последние запросы агента — что прошло, что упало, почему.',
    example: '«Что не сработало в моих последних 10 запросах?»',
    icon: Bug,
  },
  {
    name: 'list_cookbook_recipes',
    blurb: 'Готовые рецепты Brikko: legal-маскинг, CRM, support-роутер.',
    example: '«Какие готовые рецепты у Brikko для legal или CRM?»',
    icon: BookOpen,
  },
  {
    name: 'list_integrations',
    blurb: 'Инструкции для подключения Cursor, Cline, Codex CLI и прочих.',
    example: '«Как подключить меня к Brikko из Cursor или Codex CLI?»',
    icon: Plug,
  },
];

interface HowStep {
  number: string;
  title: string;
  body: string;
  icon: typeof KeyRound;
}

const STEPS: HowStep[] = [
  {
    number: '01',
    title: 'Создай MCP-токен в личном кабинете',
    body: '/app/keys → таб «MCP keys» → Create new token. Получишь токен вида mcp-brk-*. Хранится только у тебя — мы видим только хеш.',
    icon: KeyRound,
  },
  {
    number: '02',
    title: 'Скопируй промпт в Claude или Cursor',
    body: 'Любой MCP-совместимый агент: Claude Desktop, Claude Code, Cursor, Cline, Codex CLI. Кнопка «Скопировать» — выше.',
    icon: Terminal,
  },
  {
    number: '03',
    title: 'Агент сам всё настроит',
    body: 'Клонирует helper, попросит токен, добавит его в .env, подключится к https://api.brikko.ru/mcp и покажет баланс. Минута — и он знает твой контекст.',
    icon: Sparkles,
  },
];

export default function McpLandingPage() {
  return (
    <>
      <McpHero />
      <SectionDivider />
      <HowItWorks />
      <SectionDivider />
      <WhatMcpUnlocks />
      <SectionDivider />
      <SecurityNote />
      <SectionDivider />
      <FooterCta />
    </>
  );
}

/* ============================================================
 * Hero
 * ============================================================ */
function McpHero() {
  return (
    <section
      id="mcp-hero"
      style={{
        position: 'relative',
        padding: '160px 6vw 80px',
        zIndex: 2,
      }}
    >
      <div
        style={{
          position: 'relative',
          zIndex: 2,
          maxWidth: 1100,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          MCP · Model Context Protocol
        </span>

        <h1
          className="brikko-h1"
          style={{
            fontSize: 'clamp(40px, 5vw, 72px)',
            maxWidth: '22ch',
          }}
        >
          Подключи <span style={{ fontStyle: 'italic' }}>Claude</span> или{' '}
          <span style={{ fontStyle: 'italic' }}>Cursor</span> к Brikko одной
          фразой.
        </h1>

        <p className="brikko-lede" style={{ maxWidth: '64ch' }}>
          Не нужно копировать API-ключи руками и править конфиги. Скопируй
          промпт ниже — Claude сам клонирует наш helper-skill, попросит
          MCP-токен и подключится. Через минуту он знает твой баланс, лимиты
          и какую модель использовать под задачу.
        </p>

        <div
          style={{
            width: '100%',
            maxWidth: 900,
            marginBottom: 28,
          }}
        >
          <McpPromptCopyBlock prompt={ONBOARDING_PROMPT} label="prompt for claude/cursor" />
        </div>

        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 12,
            alignItems: 'center',
          }}
        >
          <Link
            href={'/app/keys' as Route}
            className="brikko-btn brikko-btn-primary"
          >
            <span>Создать MCP-токен</span>
            <span className="brikko-btn-icon-wrap" aria-hidden="true">
              <ArrowIcon />
            </span>
          </Link>
          <a
            href={HELPER_REPO_URL}
            className="brikko-btn brikko-btn-secondary"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub helper-repo
          </a>
        </div>

        <p
          style={{
            marginTop: 16,
            color: 'var(--fg-faint)',
            fontSize: 13,
          }}
        >
          Helper — public MIT. Все 7 tool&apos;ов — read-only.
        </p>
      </div>
    </section>
  );
}

/* ============================================================
 * How it works — 3 шага
 * ============================================================ */
function HowItWorks() {
  return (
    <section
      id="how-it-works"
      style={{
        position: 'relative',
        padding: '80px 6vw',
        zIndex: 2,
      }}
    >
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <header style={{ maxWidth: 720, marginBottom: 56 }}>
          <p className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Три шага
          </p>
          <h2 className="brikko-h2">Как это работает</h2>
          <p className="brikko-lede">
            Никаких installation-гайдов на десять страниц. Один токен, один
            промпт, одна минута — и агент уже видит твой Brikko-аккаунт.
          </p>
        </header>

        <ol
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: 20,
          }}
        >
          {STEPS.map((step) => {
            const Icon = step.icon;
            return (
              <li key={step.number} className="brikko-card-outer">
                <div className="brikko-card-inner">
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 24,
                    }}
                  >
                    <span
                      style={{
                        fontFamily:
                          'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                        fontSize: 12,
                        letterSpacing: '0.12em',
                        color: 'var(--fg-faint)',
                      }}
                    >
                      {step.number}
                    </span>
                    <div
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        width: 40,
                        height: 40,
                        borderRadius: 12,
                        border: '1px solid var(--hairline)',
                        background: 'var(--bg-elevated)',
                        color: 'var(--fg-primary)',
                      }}
                    >
                      <Icon
                        className="h-5 w-5"
                        strokeWidth={1.5}
                        aria-hidden="true"
                      />
                    </div>
                  </div>
                  <h3
                    style={{
                      fontFamily:
                        'var(--font-serif), Georgia, serif',
                      fontSize: 22,
                      lineHeight: 1.2,
                      fontWeight: 400,
                      margin: '0 0 12px',
                      color: 'var(--fg-primary)',
                    }}
                  >
                    {step.title}
                  </h3>
                  <p
                    style={{
                      margin: 0,
                      fontSize: 15,
                      lineHeight: 1.55,
                      color: 'var(--fg-muted)',
                    }}
                  >
                    {step.body}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

/* ============================================================
 * What MCP unlocks — сетка из 7 tool'ов
 * ============================================================ */
function WhatMcpUnlocks() {
  return (
    <section
      id="tools"
      style={{
        position: 'relative',
        padding: '80px 6vw',
        zIndex: 2,
      }}
    >
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <header style={{ maxWidth: 720, marginBottom: 56 }}>
          <p className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            7 tool&apos;ов в MCP
          </p>
          <h2 className="brikko-h2">Что Claude теперь знает про твой Brikko</h2>
          <p className="brikko-lede">
            После подключения агент получает прямой доступ к твоему аккаунту в
            read-only режиме. Спрашивай как обычно — он сам вызовет нужный
            tool.
          </p>
        </header>

        <ul
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
            gap: 16,
          }}
        >
          {TOOLS.map((tool) => {
            const Icon = tool.icon;
            return (
              <li key={tool.name} className="brikko-card-link">
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    marginBottom: 16,
                  }}
                >
                  <div
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: 36,
                      height: 36,
                      borderRadius: 10,
                      border: '1px solid var(--hairline)',
                      background: 'var(--bg-elevated)',
                      flexShrink: 0,
                    }}
                  >
                    <Icon
                      className="h-5 w-5"
                      strokeWidth={1.5}
                      aria-hidden="true"
                    />
                  </div>
                  <code
                    style={{
                      fontFamily:
                        'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                      fontSize: 14,
                      fontWeight: 500,
                      color: 'var(--fg-primary)',
                    }}
                  >
                    {tool.name}
                  </code>
                </div>
                <p
                  style={{
                    margin: '0 0 14px',
                    fontSize: 14,
                    lineHeight: 1.55,
                    color: 'var(--fg-muted)',
                  }}
                >
                  {tool.blurb}
                </p>
                <p
                  style={{
                    margin: 0,
                    padding: '10px 12px',
                    borderRadius: 8,
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--hairline)',
                    fontSize: 13,
                    lineHeight: 1.45,
                    color: 'var(--fg-faint)',
                    fontStyle: 'italic',
                  }}
                >
                  {tool.example}
                </p>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

/* ============================================================
 * Security note
 * ============================================================ */
function SecurityNote() {
  return (
    <section
      id="security"
      style={{
        position: 'relative',
        padding: '80px 6vw',
        zIndex: 2,
      }}
    >
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <header style={{ marginBottom: 32 }}>
          <p className="brikko-eyebrow">
            <span className="brikko-eyebrow-dot" aria-hidden="true" />
            Безопасность
          </p>
          <h2 className="brikko-h2">Что важно знать перед подключением</h2>
        </header>

        <ul
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          <SecurityItem>
            MCP-токен (<code className="brikko-code-inline">mcp-brk-*</code>) —
            отдельный от твоего API-ключа (
            <code className="brikko-code-inline">sk-brk-*</code>). Можно отозвать
            MCP-доступ без перевыпуска прод-ключа.
          </SecurityItem>
          <SecurityItem>
            Все 7 tool&apos;ов — read-only. Запись в файлы, изменение баланса,
            создание ключей через MCP — недоступны.
          </SecurityItem>
          <SecurityItem>
            Каждый MCP-вызов логируется в <code className="brikko-code-inline">/app/admin/audit</code>{' '}
            (видно с admin-токеном). История запросов — за 30 дней.
          </SecurityItem>
          <SecurityItem>
            Helper-repo —{' '}
            <a
              href={HELPER_REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                color: 'var(--fg-primary)',
                textDecoration: 'underline',
                textUnderlineOffset: 3,
              }}
            >
              public, MIT
            </a>
            . Перед запуском прочитай его исходник — там ~200 строк Python,
            никаких бинарников и обфускации.
          </SecurityItem>
        </ul>
      </div>
    </section>
  );
}

function SecurityItem({ children }: { children: React.ReactNode }) {
  return (
    <li
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        fontSize: 15,
        lineHeight: 1.6,
        color: 'var(--fg-muted)',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          flexShrink: 0,
          marginTop: 9,
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: 'var(--fg-faint)',
        }}
      />
      <span>{children}</span>
    </li>
  );
}

/* ============================================================
 * Footer CTA
 * ============================================================ */
function FooterCta() {
  return (
    <section
      id="cta-bottom"
      style={{
        position: 'relative',
        padding: '80px 6vw 120px',
        zIndex: 2,
      }}
    >
      <div
        style={{
          maxWidth: 720,
          margin: '0 auto',
          textAlign: 'center',
        }}
      >
        <h2
          className="brikko-h2"
          style={{
            maxWidth: '20ch',
            margin: '0 auto 28px',
          }}
        >
          Готов подключить?
        </h2>
        <p
          className="brikko-lede"
          style={{
            margin: '0 auto 32px',
            maxWidth: '50ch',
          }}
        >
          Создай MCP-токен в личном кабинете и скопируй промпт. Welcome-бонус
          200 ₽ останется на твоём балансе — хватит на первые тысячу запросов.
        </p>
        <div
          style={{
            display: 'inline-flex',
            flexWrap: 'wrap',
            gap: 12,
            justifyContent: 'center',
          }}
        >
          <Link
            href={'/app/keys' as Route}
            className="brikko-btn brikko-btn-primary"
          >
            <span>Создать MCP-токен</span>
            <span className="brikko-btn-icon-wrap" aria-hidden="true">
              <ArrowIcon />
            </span>
          </Link>
          <a
            href={HELPER_REPO_URL}
            className="brikko-btn brikko-btn-secondary"
            target="_blank"
            rel="noopener noreferrer"
          >
            Helper на GitHub
          </a>
        </div>
      </div>
    </section>
  );
}

/* ============================================================
 * Shared
 * ============================================================ */
function SectionDivider() {
  return <div className="brikko-divider" aria-hidden="true" />;
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
      aria-hidden="true"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
