import Link from 'next/link';
import type { Metadata } from 'next';
import { CheckCircle2 } from 'lucide-react';
import { BRAND } from '@/lib/brand';

/**
 * /auth/account-closed — dead-end после финального soft-delete'а.
 *
 * UX-обоснование:
 *  - НЕ используем AuthShell (с CTA «Войти») — потому что аккаунт закрыт, и login
 *    тут антирекомендация: пользователь будет хвататься за неё и получать «email
 *    not found», что хуже чем чистая dead-end-страница.
 *  - Минимальная навигация: только хедер с логотипом и `mailto:` ссылкой на саппорт.
 *    Если человек хочет вернуться — он напишет, мы реактивируем (в течение 1 года).
 *  - Тон сообщения — нейтральный, без guilt-trip'а.
 *
 * Sprint 13.5: переведён на Cream Studio v6 grayscale tokens (через .brikko-marketing
 * wrapper-класс, тот же что MarketingShell). robots: noindex сохранён.
 */

export const metadata: Metadata = {
  title: `Аккаунт закрыт — ${BRAND.name}`,
  description: 'Аккаунт Brikko закрыт. Если это ошибка — напиши на support@brikko.ai.',
  robots: { index: false, follow: false },
};

export default function AccountClosedPage() {
  return (
    <div className="brikko-marketing flex min-h-[100dvh] flex-col">
      <div className="brikko-noise" aria-hidden="true" />

      <header className="brikko-auth-bar" aria-label="Brikko">
        <Link href="/" className="brikko-brand">
          {BRAND.name}
        </Link>
      </header>

      <main className="brikko-auth-main">
        <div className="brikko-auth-card" data-testid="account-closed-card">
          <div className="brikko-auth-inner">
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 56,
                height: 56,
                borderRadius: '9999px',
                background: 'var(--bg-elevated)',
                color: 'var(--fg-primary)',
                border: '1px solid var(--hairline)',
                margin: '0 auto 24px',
              }}
              aria-hidden="true"
            >
              <CheckCircle2 className="h-7 w-7" strokeWidth={1.25} />
            </div>

            <h1
              className="brikko-auth-title"
              style={{ textAlign: 'center', fontStyle: 'italic' }}
            >
              Аккаунт закрыт
            </h1>
            <p
              className="brikko-auth-subtitle"
              style={{ textAlign: 'center' }}
            >
              Спасибо, что был с {BRAND.name}. Аккаунт удалён, API-ключи отозваны,
              доступ прекращён.
            </p>

            <div
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--hairline)',
                borderRadius: 12,
                padding: 16,
                fontSize: 13,
                color: 'var(--fg-muted)',
                lineHeight: 1.55,
                margin: '0 0 20px',
              }}
            >
              <p style={{ margin: 0 }}>
                <span style={{ color: 'var(--fg-primary)', fontWeight: 600 }}>
                  Если это ошибка
                </span>{' '}
                — напиши нам в течение года, восстановим аккаунт со всеми данными.
              </p>
              <p style={{ margin: '8px 0 0' }}>
                Через 12 месяцев после закрытия данные удаляются безвозвратно (152-ФЗ).
              </p>
            </div>

            <a
              href={`mailto:${BRAND.supportEmail}`}
              className="brikko-cta-secondary"
              style={{ width: '100%', justifyContent: 'center' }}
              data-testid="account-closed-support-link"
            >
              Написать в саппорт: {BRAND.supportEmail}
            </a>
          </div>
        </div>
      </main>

      <footer
        className="brikko-auth-foot"
        aria-label="Brikko legal"
      >
        <span className="brikko-auth-foot-copy">
          © {new Date().getFullYear()} {BRAND.name}
        </span>
      </footer>
    </div>
  );
}
