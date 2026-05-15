import { PricingCards } from '@/components/marketing/PricingCards';
// SavingsCalculator скрыт 2026-05-15 — использовал старые цены Pro 1990 / Team 4990.
// Импорт оставлен закомментированным как маркер для будущего переписывания
// под trial+subscription model (Phase 2).
// import { SavingsCalculator } from '@/components/marketing/SavingsCalculator';
import { FAQ } from '@/components/marketing/FAQ';

export const metadata = {
  title: 'Тарифы',
  description:
    '200 ₽ welcome credit на пробу. После — подписка Pro 290 ₽/мес или Team 1490 ₽/мес.',
};

/**
 * /pricing — Cream Studio v6 + trial+subscription pivot (2026-05-15).
 *
 * Hero (H1 + subhead) → PricingCards (Trial / Pro / Team) → HowItWorks → FAQ.
 *
 * UX: после rebrand'а перенесли FAQ из главной сюда (на странице /pricing
 * вопросы про billing более релевантны и снимают возражения перед оплатой).
 * Главная остаётся со своим FAQ-блоком — это не дубль, общая разметка
 * запросов одна, но оба места видят разные cohorts.
 *
 * NB 2026-05-15: SavingsCalculator скрыт — он считал экономию vs Wise
 * по старым ценам Pro 1990 / Team 4990. Переписать под trial+subscription
 * (200 ₽ welcome → Pro 290 / Team 1490) — отдельная задача Phase 2.
 */
export default function PricingPage() {
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
          Тарифы
        </span>
        <h1
          className="brikko-h1"
          style={{ fontSize: 'clamp(40px, 5vw, 80px)', maxWidth: '14ch' }}
        >
          Тариф<span className="brikko-h2-italic">ы.</span>
        </h1>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          200 ₽ welcome credit на пробу. После — подписка Pro 290 ₽/мес или
          Team 1490 ₽/мес. Top-up в рублях для постоянной работы недоступен.
        </p>
      </section>

      <div className="brikko-divider" aria-hidden="true" />

      <PricingCards showHeader={false} />

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '24px 6vw 0',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <p
          style={{
            fontSize: 12,
            color: 'var(--fg-faint)',
            margin: 0,
            textAlign: 'center',
          }}
        >
          (Калькулятор экономии временно скрыт — обновляется под новые тарифы.)
        </p>
      </div>

      <div className="brikko-divider" aria-hidden="true" />

      <HowItWorksSection />

      <div className="brikko-divider" aria-hidden="true" />

      <FAQ />
    </>
  );
}

function HowItWorksSection() {
  const steps: { title: string; body: React.ReactNode }[] = [
    {
      title: 'Регистрация',
      body: 'Создаёте аккаунт по email. Подтверждаете адрес по ссылке из письма (1-2 минуты).',
    },
    {
      title: 'Пополнение баланса',
      body: 'В кабинете на «Биллинг» нажимаете «Пополнить», выбираете сумму (от 100 ₽), платите через ЮKassa: карта или СБП. Баланс зачисляется мгновенно.',
    },
    {
      title: 'Создание API-ключа',
      body: 'В разделе «Ключи» создаёте API-ключ — строку с префиксом sk-brk-. Можно создать несколько ключей с лимитами бюджета (dev/prod).',
    },
    {
      title: 'Использование API',
      body: (
        <>
          Подставляете ключ и base URL{' '}
          <code
            style={{
              fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
              fontSize: 13,
              padding: '2px 6px',
              background: 'var(--bg-elevated)',
              borderRadius: 6,
            }}
          >
            https://api.brikko.ru/v1
          </code>{' '}
          в свой код (OpenAI SDK). Запросы выполняются мгновенно.
        </>
      ),
    },
    {
      title: 'Документы',
      body: 'После каждой оплаты — электронный чек самозанятого (формируется автоматически в «Мой налог»). Все чеки доступны в кабинете на странице «Документы».',
    },
  ];

  return (
    <section className="brikko-section" id="how-it-works">
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
          Как это работает
        </span>
        <h2 className="brikko-h2">
          <span>От регистрации </span>
          <span className="brikko-h2-italic">до 200 OK</span>
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          Brikko — digital-сервис: услуга предоставляется мгновенно после оплаты,
          без физической доставки.
        </p>
      </header>

      <ol
        style={{
          maxWidth: 880,
          margin: '0 auto',
          padding: '0 6vw',
          listStyle: 'none',
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
          position: 'relative',
          zIndex: 2,
        }}
      >
        {steps.map((s, i) => (
          <li
            key={s.title}
            style={{
              display: 'flex',
              gap: 24,
              padding: '20px 0',
              borderBottom: i === steps.length - 1 ? 'none' : '1px solid var(--hairline)',
            }}
          >
            <span
              style={{
                flexShrink: 0,
                width: 36,
                height: 36,
                borderRadius: 9999,
                border: '1px solid var(--hairline)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                fontSize: 13,
                fontWeight: 500,
                color: 'var(--fg-primary)',
              }}
            >
              0{i + 1}
            </span>
            <div>
              <h3
                style={{
                  fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                  fontWeight: 400,
                  fontSize: 22,
                  letterSpacing: '-0.01em',
                  color: 'var(--fg-primary)',
                  margin: '0 0 8px',
                }}
              >
                {s.title}
              </h3>
              <p style={{ fontSize: 15, lineHeight: 1.6, color: 'var(--fg-muted)', margin: 0 }}>
                {s.body}
              </p>
            </div>
          </li>
        ))}
      </ol>

      <div
        style={{
          maxWidth: 880,
          margin: '40px auto 0',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <div
          style={{
            border: '1px solid var(--hairline)',
            background: 'var(--bg-elevated)',
            borderRadius: 24,
            padding: 24,
          }}
        >
          <h3
            style={{
              fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
              fontWeight: 400,
              fontSize: 20,
              color: 'var(--fg-primary)',
              margin: '0 0 8px',
              letterSpacing: '-0.01em',
            }}
          >
            Если что-то не работает
          </h3>
          <p style={{ fontSize: 14, color: 'var(--fg-muted)', lineHeight: 1.55, margin: 0 }}>
            Напишите на{' '}
            <a
              href="mailto:gridchin.pismorf@gmail.com"
              style={{ color: 'var(--fg-primary)', textDecoration: 'underline' }}
            >
              gridchin.pismorf@gmail.com
            </a>
            . Срок ответа — не более 10 рабочих дней. При технических сбоях, повлиявших
            на ваши запросы, компенсация выплачивается на баланс в течение 14 дней.
          </p>
          <p style={{ marginTop: 12, fontSize: 13, color: 'var(--fg-faint)' }}>
            Подробные условия —{' '}
            <a
              href="/legal/oferta"
              style={{ color: 'var(--fg-muted)', textDecoration: 'underline' }}
            >
              в публичной оферте
            </a>
            . Реквизиты —{' '}
            <a
              href="/legal/info"
              style={{ color: 'var(--fg-muted)', textDecoration: 'underline' }}
            >
              /legal/info
            </a>
            .
          </p>
        </div>
      </div>
    </section>
  );
}
