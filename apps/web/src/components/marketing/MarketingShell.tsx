'use client';

/**
 * MarketingShell — единый client-wrapper для всех публичных страниц после
 * Cream Studio v6 rebrand'а (Sprint 13, 2026-05-02).
 *
 * Что делает:
 *   - Hydrate theme из localStorage / prefers-color-scheme и применяет
 *     [data-theme] на <html>. Один источник истины для marketing + cream-preview.
 *   - Рендерит fixed top-bar (Brikko + hamburger overlay + theme-toggle + login).
 *   - Рендерит TrafficCanvas — фоновая анимация трафика через router.
 *   - Рендерит Footer (минималистичный grayscale).
 *   - Эффект: vending-shell для children. children рендерятся как .marketingMain
 *     с z-index выше canvas'а.
 *
 * UX-обоснование:
 *   - Один shell на все marketing-страницы → консистентность header/footer/theme.
 *   - Top-bar inline (не glass-pill): минимизирует визуальный шум, фокус
 *     на контенте. Hamburger по центру — намеренно нестандарт, Brikko-DNA.
 *   - Theme-toggle в правом углу: ожидаемое место. Persist через localStorage,
 *     fallback prefers-color-scheme.
 *
 * Что НЕ делает:
 *   - НЕ оборачивает /preview/cream — у того свой shell с дополнительными
 *     эффектами (BootSequence, ReadingProgress, CursorDot, Lenis-scroll).
 *     Marketing'у эти эффекты не нужны (overhead JS на SEO-страницах).
 *   - НЕ загружает Lenis / GSAP — анимации ограничены простыми CSS-transitions.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { Route } from 'next';
import { TrafficCanvas } from './TrafficCanvas';
import { Footer } from './Footer';
import { BRAND } from '@/lib/brand';
import { useTheme } from '@/hooks/useTheme';

const MENU_LINKS: { href: string; label: string }[] = [
  { href: '/models', label: 'Модели' },
  { href: '/pricing', label: 'Тарифы' },
  { href: '/playground', label: 'Playground' },
  { href: '/docs', label: 'Документация' },
  { href: '/integrations', label: 'Интеграции' },
  { href: '/mcp', label: 'MCP' },
  { href: '/faq', label: 'FAQ' },
];

interface Props {
  children: React.ReactNode;
}

export function MarketingShell({ children }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { theme, toggle: toggleTheme } = useTheme();

  // Hamburger ESC + scroll lock.
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [menuOpen]);

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:bg-fg-primary focus:px-4 focus:py-2 focus:text-bg-base"
      >
        Перейти к содержимому
      </a>

      <TrafficCanvas themeKey={theme} />

      <div className="brikko-noise" aria-hidden="true" />

      <header className="brikko-top-bar" aria-label="Brikko navigation">
        <Link href="/" className="brikko-brand">
          {BRAND.name}
        </Link>

        <button
          type="button"
          className={`brikko-hamburger ${menuOpen ? 'brikko-hamburger-open' : ''}`}
          aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'}
          aria-expanded={menuOpen}
          aria-controls="brikko-menu-overlay"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <span className="brikko-hamburger-line" aria-hidden="true" />
          <span className="brikko-hamburger-line" aria-hidden="true" />
          <span className="brikko-hamburger-line" aria-hidden="true" />
        </button>

        <div className="brikko-top-right">
          <button
            type="button"
            aria-label={`Переключить на ${theme === 'light' ? 'тёмную' : 'светлую'} тему`}
            onClick={toggleTheme}
            className="brikko-theme-toggle"
          >
            {theme === 'light' ? <MoonIcon /> : <SunIcon />}
          </button>
          <Link className="brikko-top-login" href="/login">
            Войти
          </Link>
        </div>
      </header>

      <div
        id="brikko-menu-overlay"
        className={`brikko-menu-overlay ${menuOpen ? 'brikko-menu-overlay-open' : ''}`}
        aria-hidden={!menuOpen}
        onClick={(e) => {
          if (e.target === e.currentTarget) setMenuOpen(false);
        }}
      >
        <nav className="brikko-menu-nav" aria-label="Brikko sections">
          <ul className="brikko-menu-list">
            {MENU_LINKS.map((link, i) => (
              <li
                key={link.href}
                className="brikko-menu-item"
                style={{ transitionDelay: menuOpen ? `${100 + i * 50}ms` : '0ms' }}
              >
                <Link
                  className="brikko-menu-link"
                  href={link.href as Route}
                  onClick={() => setMenuOpen(false)}
                  tabIndex={menuOpen ? 0 : -1}
                >
                  <span className="brikko-menu-index">0{i + 1}</span>
                  <span className="brikko-menu-label">{link.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <main id="main" className="brikko-main">
        {children}
      </main>

      <Footer />
    </>
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
