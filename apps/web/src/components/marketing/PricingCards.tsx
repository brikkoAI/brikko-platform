import Link from 'next/link';
import type { Route } from 'next';
import { Check } from 'lucide-react';

/**
 * PricingCards — subscription pivot (CEO 2026-05-15).
 *
 * Architectural shift: PAYG больше не «постоянный entry tier». Это onboarding
 * trial (welcome 100 ₽ signup + 100 ₽ card-link = 200 ₽). Когда trial кончился —
 * единственный путь к paid usage — подписка (Pro 290 / Team 1490). Top-up в
 * рублях для PAYG больше не существует.
 *
 * Линейка (слева → направо, desktop):
 *   - Trial (слева, subdued)        — «Бесплатно», 200 ₽ welcome, gateway-tier.
 *   - Pro (центр, HIGHLIGHTED)      — 290 ₽/мес, badge «ПОПУЛЯРНЫЙ».
 *   - Team (справа, secondary)      — 1 490 ₽/мес.
 *   - Enterprise — mailto-note под cards.
 *
 * UX-обоснование hierarchy:
 *   - Pro в центре + highlighted — recommended path (Stripe pattern). 290 ₽ это
 *     impulse-purchase-zone (≈ цена 1 кофе), мы хотим максимум конверсии сюда,
 *     не в trial. Поэтому Pro визуально доминирует, а Trial — приглушён.
 *   - Trial subdued (background чуть светлее основного card-bg, без shadow,
 *     без translateY): visual hint что это «попробовать», не «подписаться».
 *     Если сделать Trial обычной secondary-карточкой — он визуально равен
 *     Team'у, что вводит в заблуждение (Team paid, Trial free).
 *   - Цена в Trial — слово «Бесплатно» вместо «0 ₽» в той же размерной шкале.
 *     0 ₽ читается как «цена есть, она нулевая», что психологически приравнивает
 *     trial к Pro/Team как ещё одну подписку. «Бесплатно» — отдельная категория.
 *   - 4 bullets на карточку — потолок для быстрого scan (одного фокус-такта).
 *
 * См. также:
 *   - /pricing страница использует тот же компонент (showHeader=false).
 *   - /signup поддерживает ?tier=pro|team — CTA из Pro/Team ведут с пометкой,
 *     после verify-email юзер попадает на /app/billing?action=subscribe&tier=…
 */

interface PricingCardsProps {
  /**
   * Показывать ли встроенный header «Тарифы». На главной (`/`) — true.
   * На странице `/pricing` — false (там свой hero-блок).
   */
  showHeader?: boolean;
}

type TierId = 'trial' | 'pro' | 'team';
type TierVariant = 'trial' | 'standard' | 'highlighted';

interface Tier {
  id: TierId;
  variant: TierVariant;
  name: string;
  /** Главное слово на месте цены. Для Trial — «Бесплатно». */
  price: string;
  /** Хвост рядом с ценой («в месяц»). Trial — пусто. */
  priceUnit: string;
  subline: string;
  bullets: string[];
  ctaLabel: string;
  ctaHref: Route;
  badge?: string;
}

const TIERS: Tier[] = [
  {
    id: 'trial',
    variant: 'trial',
    name: 'Попробовать',
    price: 'Бесплатно',
    priceUnit: '',
    subline: 'до 200 ₽ welcome credit',
    bullets: [
      '100 ₽ при регистрации (5 000 запросов)',
      '+100 ₽ за привязку карты (ещё 5 000 запросов)',
      'Без подписки — попробовать продукт',
      'После — нужна подписка (Pro или Team)',
    ],
    ctaLabel: 'Начать бесплатно',
    ctaHref: '/signup' as Route,
  },
  {
    id: 'pro',
    variant: 'highlighted',
    name: 'Pro',
    price: '290 ₽',
    priceUnit: 'в месяц',
    subline: 'Безлимит для одного пользователя',
    bullets: [
      'Unlimited маскинг',
      'Все entities (ИНН, паспорт, ФИО, СНИЛС, ОГРН, телефон, банк)',
      'Natasha NER для русских ФИО (морфология)',
      '5 устройств / API-ключей',
    ],
    ctaLabel: 'Оформить Pro',
    ctaHref: '/signup?tier=pro' as Route,
    badge: 'Популярный',
  },
  {
    id: 'team',
    variant: 'standard',
    name: 'Team',
    price: '1 490 ₽',
    priceUnit: 'в месяц',
    subline: 'Для команд до 10 человек',
    bullets: [
      'Всё из Pro + 10 пользователей',
      'Общие пресеты команды',
      'Audit log (90 дней retention)',
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
            <span>Тарифы</span>
            <span className="brikko-h2-italic">.</span>
          </h2>
          <p className="brikko-lede" style={{ marginBottom: 0 }}>
            Попробуйте 200 ₽ free trial. Когда понравится — выберите подписку.
            Pay-as-you-go без подписки невозможен (это только для пробы продукта).
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
  const isHighlighted = tier.variant === 'highlighted';
  const isTrial = tier.variant === 'trial';

  const outerClass = [
    'brikko-card-outer',
    isHighlighted ? 'brikko-pricing-tier--highlighted' : '',
    isTrial ? 'brikko-pricing-tier--trial' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <article
      className={outerClass}
      data-testid={`pricing-tier-${tier.id}`}
      style={{
        display: 'flex',
        position: 'relative',
        minWidth: 0,
        ...(isHighlighted
          ? {
              background: 'var(--accent-1)',
              border: '1px solid var(--accent-1)',
              // Stripe-pattern recommended: чуть приподнят над соседями.
              // На mobile transform убирается через CSS media-query
              // brikko-pricing-tier--highlighted (см. brikko-marketing.css).
              transform: 'translateY(-8px)',
              boxShadow: '0 12px 32px -8px rgba(0, 0, 0, 0.12)',
            }
          : isTrial
            ? {
                // Trial — subdued. Без shadow, без translateY, бордер мягче.
                // Тон чуть отличается от обычной карточки — сигнал «это иной
                // визуальный язык, не paid tier».
                background: 'var(--bg-elevated)',
                borderColor: 'var(--hairline)',
                opacity: 0.96,
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
          // Trial: inner-фон чуть светлее, чтобы карточка читалась как «лёгкая».
          ...(isTrial ? { background: 'var(--bg-elevated)' } : {}),
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
                fontSize: isHighlighted
                  ? 'clamp(44px, 5vw, 64px)'
                  : isTrial
                    ? 'clamp(32px, 3.6vw, 44px)'
                    : 'clamp(36px, 4vw, 52px)',
                lineHeight: 1,
                letterSpacing: '-0.03em',
                color: 'var(--fg-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {tier.price}
            </span>
            {tier.priceUnit ? (
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
            ) : null}
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
          data-testid={`pricing-cta-${tier.id}`}
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
