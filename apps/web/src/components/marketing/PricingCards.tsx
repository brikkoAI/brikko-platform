import Link from 'next/link';
import type { Route } from 'next';
import { Check } from 'lucide-react';

/**
 * PricingCards — 3-tier (Cream Studio v6, 2026-05-15).
 *
 * CEO решение 2026-05-15: возвращаем подписочные тарифы. Pay-as-you-go
 * остаётся как entry point (highlighted/recommended в центре), Pro/Team
 * — подписки для активных пользователей и команд.
 *
 * Линейка:
 *   - Pay-as-you-go (центр, recommended) — 0,02 ₽/запрос, low commitment.
 *   - Pro (слева) — 290 ₽/мес, unlimited для физика.
 *   - Team (справа) — 1 490 ₽/мес, до 10 человек + audit log + MCP.
 *   - Enterprise — mailto-link под cards, без цены на лендинге.
 *
 * UX-обоснование:
 *   - Pay-as-you-go в центре, а не первой слева: визуально-весовой центр
 *     ряда (золотое сечение для тройки), highlight border + чуть приподнят
 *     (translateY) — Stripe-like recommended pattern.
 *   - На mobile (column stack) — Pay-as-you-go первой, потому что это
 *     entry-friendly tier (нулевой commitment).
 *   - 4 bullets на карточку — потолок для быстрого scan (одного фокус-такта
 *     без re-fixation). 5+ заставляет читателя «считать», что увеличивает
 *     decision-fatigue на pricing-screen.
 *   - Enterprise как mailto-note, а не 4-я карточка — это honest signal,
 *     что enterprise-deal делается per-customer, не self-serve. Маркетингу
 *     4 карточки в ряд читаются как «у нас всё для всех», что для соло-стартапа
 *     с runway 10 мес — false promise.
 *
 * Связанные изменения:
 *   - /pricing страница (showHeader=false) использует тот же компонент.
 *   - FAQ.tsx — может содержать упоминания старых tier; переписать отдельной
 *     задачей.
 */

interface PricingCardsProps {
  /**
   * Показывать ли встроенный header «Тарифы». На главной (`/`) — true.
   * На странице `/pricing` — false (там свой hero-блок).
   */
  showHeader?: boolean;
}

type TierId = 'pro' | 'payg' | 'team';

interface Tier {
  id: TierId;
  name: string;
  price: string;
  priceUnit: string;
  subline: string;
  bullets: string[];
  ctaLabel: string;
  ctaHref: Route;
  highlighted?: boolean;
  badge?: string;
}

const TIERS: Tier[] = [
  {
    id: 'pro',
    name: 'Pro',
    price: '290 ₽',
    priceUnit: 'в месяц',
    subline: 'Безлимит для одного пользователя',
    bullets: [
      'Unlimited маскинг',
      'Все entities (ИНН, паспорт, ФИО, СНИЛС, ОГРН, телефон, банк)',
      'Natasha NER для русских ФИО',
      '5 устройств / API-ключей',
    ],
    ctaLabel: 'Оформить Pro',
    ctaHref: '/signup?tier=pro' as Route,
  },
  {
    id: 'payg',
    name: 'Pay-as-you-go',
    price: '0,02 ₽',
    priceUnit: 'за запрос',
    subline: '50 запросов за 1 ₽ · 5 000 за 100 ₽',
    bullets: [
      '100 запросов в день бесплатно навсегда',
      'Welcome 100 ₽ при регистрации',
      'Top-up рублями через ЮKassa, без VISA / MC',
      'Все 6 артефактов через единый API-ключ',
    ],
    ctaLabel: 'Начать бесплатно',
    ctaHref: '/signup' as Route,
    highlighted: true,
    badge: 'Рекомендуем',
  },
  {
    id: 'team',
    name: 'Team',
    price: '1 490 ₽',
    priceUnit: 'в месяц',
    subline: 'Для команд до 10 человек',
    bullets: [
      'Всё из Pro + 10 пользователей',
      'Общие пресеты команды',
      'Audit log',
      'MCP-серверы Bitrix24 / 1С',
    ],
    ctaLabel: 'Оформить Team',
    ctaHref: '/signup?tier=team' as Route,
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
            <span>Платите так, как </span>
            <span className="brikko-h2-italic">удобно.</span>
          </h2>
          <p className="brikko-lede" style={{ marginBottom: 0 }}>
            Начните с pay-as-you-go без подписки. Если запросов в день станет
            больше — Pro закроет личное использование, Team — команду до 10 человек.
          </p>
        </header>
      ) : null}

      <div
        style={{
          maxWidth: 1200,
          margin: '0 auto',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <div className="brikko-pricing-grid">
          {TIERS.map((tier) => (
            <PricingTierCard key={tier.id} tier={tier} />
          ))}
        </div>
      </div>

      <EnterpriseNote />
    </section>
  );
}

function PricingTierCard({ tier }: { tier: Tier }) {
  const isHighlighted = tier.highlighted ?? false;

  return (
    <article
      className={
        isHighlighted
          ? 'brikko-card-outer brikko-pricing-tier--highlighted'
          : 'brikko-card-outer'
      }
      style={{
        display: 'flex',
        position: 'relative',
        minWidth: 0,
        ...(isHighlighted
          ? {
              background: 'var(--accent-1)',
              border: '1px solid var(--accent-1)',
              // Чуть приподнят над соседями — Stripe-like recommended pattern.
              // На mobile (<768px) transform убирается через CSS media-query
              // в brikko-pricing-tier--highlighted: в column-stack нет смысла
              // «приподнимать» над соседями, которые сверху/снизу.
              transform: 'translateY(-8px)',
              boxShadow: '0 12px 32px -8px rgba(0, 0, 0, 0.12)',
            }
          : {}),
      }}
    >
      <div
        className="brikko-card-inner"
        style={{
          padding: isHighlighted ? '36px 28px 32px' : '32px 28px 28px',
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          minWidth: 0,
          gap: 20,
        }}
      >
        {tier.badge ? (
          <span
            style={{
              position: 'absolute',
              top: -10,
              left: '50%',
              transform: 'translateX(-50%)',
              background: 'var(--fg-primary)',
              color: 'var(--bg-base)',
              padding: '4px 14px',
              borderRadius: 9999,
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: '0.2em',
              textTransform: 'uppercase',
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              whiteSpace: 'nowrap',
            }}
          >
            {tier.badge}
          </span>
        ) : null}

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
            {tier.name}
          </p>

          <div
            style={{
              marginTop: 12,
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'center',
              gap: 8,
              flexWrap: 'wrap',
            }}
          >
            <span
              style={{
                fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                fontWeight: 400,
                fontSize: isHighlighted ? 'clamp(44px, 5vw, 64px)' : 'clamp(36px, 4vw, 52px)',
                lineHeight: 1,
                letterSpacing: '-0.03em',
                color: 'var(--fg-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {tier.price}
            </span>
            <span
              style={{
                fontFamily:
                  '"Source Serif 4", "PP Editorial New", Georgia, serif',
                fontStyle: 'italic',
                fontWeight: 400,
                fontSize: 'clamp(14px, 1.6vw, 18px)',
                color: 'var(--fg-muted)',
                letterSpacing: '-0.01em',
              }}
            >
              {tier.priceUnit}
            </span>
          </div>

          <p
            style={{
              marginTop: 12,
              fontSize: 13,
              lineHeight: 1.5,
              color: 'var(--fg-muted)',
              fontVariantNumeric: 'tabular-nums',
              minHeight: 40,
            }}
          >
            {tier.subline}
          </p>
        </header>

        <div
          style={{
            paddingTop: 20,
            borderTop: '1px solid var(--hairline)',
          }}
        >
          <ul
            style={{
              padding: 0,
              margin: 0,
              listStyle: 'none',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            {tier.bullets.map((text) => (
              <li
                key={text}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                  fontSize: 14,
                  lineHeight: 1.5,
                  color: 'var(--fg-primary)',
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    flexShrink: 0,
                    width: 20,
                    height: 20,
                    marginTop: 1,
                    borderRadius: 9999,
                    border: '1px solid var(--hairline)',
                    background: isHighlighted ? 'var(--bg-base)' : 'var(--bg-tier-2)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--fg-primary)',
                  }}
                >
                  <Check size={12} strokeWidth={2} />
                </span>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>

        <div style={{ flex: 1, minHeight: 8 }} />

        <Link
          href={tier.ctaHref}
          className={
            isHighlighted
              ? 'brikko-btn brikko-btn-primary'
              : 'brikko-btn brikko-btn-secondary'
          }
          style={{ justifyContent: 'center', width: '100%' }}
        >
          {tier.ctaLabel}
        </Link>
      </div>
    </article>
  );
}

function EnterpriseNote() {
  return (
    <div
      style={{
        maxWidth: 1200,
        margin: '40px auto 0',
        padding: '0 6vw',
        position: 'relative',
        zIndex: 2,
      }}
    >
      <p
        style={{
          textAlign: 'center',
          fontSize: 14,
          lineHeight: 1.6,
          color: 'var(--fg-muted)',
          margin: 0,
        }}
      >
        Нужно больше — выделенный SLA, on-prem, корпоративный договор? Напишите:{' '}
        <a
          href="mailto:hello@brikko.ru"
          style={{
            color: 'var(--fg-primary)',
            textDecoration: 'underline',
            textDecorationThickness: 1,
            textUnderlineOffset: 3,
          }}
        >
          hello@brikko.ru
        </a>
      </p>
    </div>
  );
}
