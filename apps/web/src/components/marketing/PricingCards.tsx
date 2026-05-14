import Link from 'next/link';
import { CreditCard, Gift, Key, Sparkles } from 'lucide-react';

/**
 * PricingCards — Cream Studio v6 + pay-per-use pivot (2026-05-14).
 *
 * CEO confirmation 2026-05-14: убираем Free/Pro 290/Team 1990/Enterprise
 * subscription-модель. Одна крупная карточка с pay-as-you-go 0,02 ₽/запрос.
 *
 * Почему pay-per-use вместо subscription:
 *   1. Self-serve adoption — нулевой commitment, нет «а вдруг не нужно подписываться».
 *   2. 6 distribution channels (Shield/Studio/CLI/Skill/n8n/Presidio) делят один
 *      backend → один API ключ для всего, один тариф для всего.
 *   3. Welcome 100 ₽ покрывает 5 000 запросов — за это время пользователь
 *      решает «брать или нет» без риска.
 *   4. 100 запросов/день free навсегда — anchoring под одиночек, у которых
 *      pet-проекты с маленькой нагрузкой.
 *
 * UX-обоснование одной карточки:
 *   - 4 карточки subscription tier ставили выбор «какой тариф» раньше выбора
 *     «вообще регистрироваться». Pay-per-use снимает этот выбор полностью.
 *   - Крупная «0,02 ₽» в Source Serif 4 (как Hero H1) — типографический
 *     якорь, привлекающий fovea в первые 200ms scroll'а.
 *   - Sub-«50 запросов за 1 ₽» переводит абстрактные доли копейки в шкалу
 *     «сколько за 1 ₽» — единица измерения, которую мозг чтения «считает»
 *     быстрее чем 0,02.
 *
 * Связанные изменения:
 *   - /pricing страница (showHeader=false) — обновлена тот же блок.
 *   - FAQ.tsx — не трогаем, там Pro/Team в контексте старой gateway-модели
 *     не упоминаются (Pro Privacy ≠ subscription tier — это feature-флаг).
 */

interface PricingCardsProps {
  /**
   * Показывать ли встроенный header «Тарифы / Pay-as-you-go».
   * На главной (`/`) — true (это самостоятельная секция). На странице
   * `/pricing` — false, чтобы не дублировать H1 hero-блока.
   */
  showHeader?: boolean;
}

interface Bullet {
  Icon: typeof Sparkles;
  text: string;
}

const BULLETS: Bullet[] = [
  {
    Icon: Sparkles,
    text: '100 запросов в день бесплатно навсегда — для одиночек',
  },
  {
    Icon: Gift,
    text: 'Welcome 100 ₽ при регистрации = 5 000 запросов попробовать',
  },
  {
    Icon: CreditCard,
    text: 'Top-up через ЮKassa, минимум 100 ₽, без карт VISA / MC',
  },
  {
    Icon: Key,
    text: 'Один тариф для Shield, CLI, Studio, n8n, Skill — единый API ключ',
  },
];

export function PricingCards({ showHeader = true }: PricingCardsProps = {}) {
  return (
    <section id="pricing" className="brikko-section">
      {showHeader ? (
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
            Тарифы
          </span>
          <h2 className="brikko-h2">
            <span>Pay-as-</span>
            <span className="brikko-h2-italic">you-go.</span>
          </h2>
          <p className="brikko-lede" style={{ marginBottom: 0 }}>
            Никаких подписок. Платишь только за то, что используешь. Один тариф
            для всех каналов Brikko: Shield в браузере, Studio, CLI, n8n, Claude
            Code Skill.
          </p>
        </header>
      ) : null}

      <div
        style={{
          maxWidth: 720,
          margin: '0 auto',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <PayPerUseCard />
      </div>

      <ProPassNote />
    </section>
  );
}

function PayPerUseCard() {
  return (
    <article
      className="brikko-card-outer"
      style={{
        background: 'var(--accent-1)',
        border: '1px solid var(--accent-1)',
      }}
    >
      <div
        className="brikko-card-inner"
        style={{
          padding: '48px 40px 40px',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: -10,
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'var(--accent-1)',
            color: 'var(--bg-base)',
            padding: '4px 14px',
            borderRadius: 9999,
            fontSize: 10,
            fontWeight: 500,
            letterSpacing: '0.2em',
            textTransform: 'uppercase',
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          }}
        >
          Pay-as-you-go
        </span>

        <header style={{ textAlign: 'center' }}>
          <p
            style={{
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 11,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--fg-muted)',
              margin: 0,
            }}
          >
            Единый тариф
          </p>

          <div
            style={{
              marginTop: 16,
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'center',
              gap: 12,
              flexWrap: 'wrap',
            }}
          >
            <span
              style={{
                fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                fontWeight: 400,
                fontSize: 'clamp(56px, 8vw, 96px)',
                lineHeight: 1,
                letterSpacing: '-0.035em',
                color: 'var(--fg-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              0,02 ₽
            </span>
            <span
              style={{
                fontFamily:
                  '"Source Serif 4", "PP Editorial New", Georgia, serif',
                fontStyle: 'italic',
                fontWeight: 400,
                fontSize: 'clamp(20px, 2.4vw, 28px)',
                color: 'var(--fg-muted)',
                letterSpacing: '-0.01em',
              }}
            >
              за запрос
            </span>
          </div>

          <p
            style={{
              marginTop: 16,
              fontSize: 15,
              lineHeight: 1.55,
              color: 'var(--fg-muted)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            50 запросов за 1 ₽ · 5 000 запросов за 100 ₽
          </p>
        </header>

        <div
          style={{
            margin: '32px 0 0',
            paddingTop: 32,
            borderTop: '1px solid var(--hairline)',
          }}
        >
          <ul
            style={{
              padding: 0,
              listStyle: 'none',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            {BULLETS.map(({ Icon, text }) => (
              <li
                key={text}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                  fontSize: 15,
                  lineHeight: 1.5,
                  color: 'var(--fg-primary)',
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    flexShrink: 0,
                    width: 32,
                    height: 32,
                    borderRadius: 9999,
                    border: '1px solid var(--hairline)',
                    background: 'var(--bg-base)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--fg-primary)',
                  }}
                >
                  <Icon size={16} strokeWidth={1.5} />
                </span>
                <span style={{ paddingTop: 6 }}>{text}</span>
              </li>
            ))}
          </ul>
        </div>

        <div style={{ marginTop: 36, display: 'flex', justifyContent: 'center' }}>
          <Link
            href="/signup"
            className="brikko-btn brikko-btn-primary"
            style={{ minWidth: 240, justifyContent: 'center' }}
          >
            Создать аккаунт
          </Link>
        </div>

        <p
          style={{
            marginTop: 16,
            textAlign: 'center',
            fontSize: 13,
            color: 'var(--fg-muted)',
          }}
        >
          Чек самозанятого (НПД) после каждого пополнения через ЮKassa.
        </p>
      </div>
    </article>
  );
}

function ProPassNote() {
  return (
    <div
      style={{
        maxWidth: 720,
        margin: '32px auto 0',
        padding: '0 6vw',
        position: 'relative',
        zIndex: 2,
      }}
    >
      <div
        style={{
          border: '1px dashed var(--hairline)',
          borderRadius: 20,
          padding: '20px 24px',
          background: 'transparent',
          fontSize: 14,
          lineHeight: 1.55,
          color: 'var(--fg-muted)',
          textAlign: 'center',
        }}
      >
        Когда станет много запросов в день — мы добавим Pro Pass с unlimited.
        Сейчас, по факту adoption, pay-per-use удобнее всем.
      </div>
    </div>
  );
}
