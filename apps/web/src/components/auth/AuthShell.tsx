'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { BRAND } from '@/lib/brand';
import { useTheme } from '@/hooks/useTheme';

interface AuthShellProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  /** Если задан — рендерится между card'ой и footer-link'ом, обычно для secondary-CTA. */
  footerLink?: ReactNode;
}

/**
 * AuthShell — cream-style auth wrapper (Sprint 13.5, 2026-05-02).
 *
 * UX-обоснование:
 *   - Top-bar минимальный (только Brikko-mark + theme-toggle): auth = focused
 *     task, hamburger со всеми marketing-разделами уводил бы пользователя
 *     прямо посреди регистрации. Это match'ит Stripe / Linear / Vercel.
 *   - НЕТ TrafficCanvas (фоновой particle-анимации) — фокус на форме, минус
 *     визуальный шум, минус ~15kb JS на маленьких pages.
 *   - Card centered, max-width 420px — 2-3 поля + CTA читаются без скролла,
 *     не растягивается на широких мониторах.
 *   - Использует те же CSS-vars (--bg-base / --fg-primary / --hairline / etc),
 *     что marketing — single source of truth для темы. brikko-marketing класс
 *     на root'е активирует body:has-rule в globals.css → CSS-vars применяются
 *     к фону и тексту body.
 *   - Заголовок — PP Editorial / Source Serif 4 italic (через .brikko-h2-italic).
 *     Marketing использует тот же font-stack — auth воспринимается как
 *     продолжение бренда, не отдельный мини-сайт.
 *
 * a11y:
 *   - Skip-link на #auth-main (как в MarketingShell).
 *   - Theme-toggle: aria-label с текущим состоянием. focus-visible — наследует
 *     `.brikko-marketing *:focus-visible` rule (outline accent-1 + offset 3px).
 *   - h1 рендерится один раз, subtitle — параграф (не h2), чтобы heading-tree
 *     остался плоским и читаемым screen-reader'ом.
 *
 * SSR-safe: useTheme при первом рендере отдаёт `light` + `ready=false`,
 * иконку рендерим всегда (Moon в light, Sun в dark) — она не создаёт hydration
 * mismatch'а потому что `theme` стартует с `light` и на сервере, и на клиенте.
 */
export function AuthShell({ title, subtitle, children, footerLink }: AuthShellProps) {
  const { theme, toggle } = useTheme();

  return (
    <div className="brikko-marketing flex min-h-[100dvh] flex-col">
      <a
        href="#auth-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:bg-fg-primary focus:px-4 focus:py-2 focus:text-bg-base"
      >
        Перейти к содержимому
      </a>

      <div className="brikko-noise" aria-hidden="true" />

      <header className="brikko-auth-bar" aria-label="Brikko">
        <Link href="/" className="brikko-brand">
          {BRAND.name}
        </Link>
        <button
          type="button"
          aria-label={`Переключить на ${theme === 'light' ? 'тёмную' : 'светлую'} тему`}
          onClick={toggle}
          className="brikko-theme-toggle"
        >
          {theme === 'light' ? <MoonIcon /> : <SunIcon />}
        </button>
      </header>

      <main id="auth-main" className="brikko-auth-main">
        <div className="brikko-auth-card">
          <div className="brikko-auth-inner">
            <h1 className="brikko-auth-title">{title}</h1>
            {subtitle ? <p className="brikko-auth-subtitle">{subtitle}</p> : null}
            <div className="brikko-auth-body">{children}</div>
          </div>
        </div>
        {footerLink ? <div className="brikko-auth-footer-link">{footerLink}</div> : null}
      </main>

      <footer className="brikko-auth-foot" aria-label="Brikko legal">
        <Link href={'/legal/oferta' as never} className="brikko-auth-foot-link">
          Оферта
        </Link>
        <span aria-hidden="true" className="brikko-auth-foot-sep">·</span>
        <Link href={'/legal/privacy' as never} className="brikko-auth-foot-link">
          Конфиденциальность
        </Link>
        <span aria-hidden="true" className="brikko-auth-foot-sep">·</span>
        <span className="brikko-auth-foot-copy">
          © {new Date().getFullYear()} {BRAND.name}
        </span>
      </footer>
    </div>
  );
}

function SunIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v1.5" />
      <path d="M12 19.5V21" />
      <path d="M3 12h1.5" />
      <path d="M19.5 12H21" />
      <path d="M5.636 5.636l1.06 1.06" />
      <path d="M17.303 17.303l1.06 1.06" />
      <path d="M5.636 18.364l1.06-1.06" />
      <path d="M17.303 6.697l1.06-1.06" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
    </svg>
  );
}
