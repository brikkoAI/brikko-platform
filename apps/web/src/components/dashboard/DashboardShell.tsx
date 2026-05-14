'use client';

/**
 * DashboardShell — единый client-wrapper для /app/* (Sprint 13.6, 2026-05-02).
 *
 * UX-обоснование:
 *   - Sidebar (а не top-bar как marketing) — dashboard это long-session
 *     productivity tool с 6 разделами; пользователю нужен постоянный быстрый
 *     доступ к навигации, а не «открыть hamburger каждый раз». Match'ит Linear,
 *     Vercel, Stripe.
 *   - Active link через accent-1 vertical bar + лёгкий bg-elevated: визуально
 *     спокойнее, чем full bg pill.
 *   - User-block + theme-toggle внизу sidebar'а — Notion / Slack pattern,
 *     гарантированное место для logout без конкуренции с основной навигацией.
 *   - На mobile sidebar схлопывается, появляется bottom-tab-bar (5 пунктов,
 *     Team/Settings под «Ещё»). Match'ит iOS-tab-bar UX.
 *   - Theme `data-theme` persists через `useTheme()` (тот же hook, что
 *     marketing/auth) → CEO зашёл в light → dashboard light.
 *
 * Что НЕ делает:
 *   - НЕ оборачивает /preview/cream — у того свой shell.
 *   - НЕ загружает TrafficCanvas (фоновую анимацию) — dashboard сидячая,
 *     долгая сессия, лишний шум вреден productivity. Marketing → разовый
 *     impact, dashboard → ежедневная работа.
 *   - НЕ блокирует children-render до theme.ready — theme стартует с light
 *     SSR-side и hydrate-step переключит на сохранённую тему без mismatch'а.
 *
 * Замена legacy-компонентов:
 *   - Sidebar.tsx (indigo, Tailwind classes) → встроен сюда
 *   - Topbar.tsx (indigo balance pill) → встроен сюда (balance в sidebar bottom + topbar context)
 *   - MobileNav.tsx → встроен сюда
 */

import Link from 'next/link';
import type { Route } from 'next';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  LayoutDashboard,
  Key,
  BarChart3,
  CreditCard,
  Users,
  Settings,
  Shield,
  LogOut,
  MoreHorizontal,
  ExternalLink,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { useTheme } from '@/hooks/useTheme';
import { useAccount, useBalance, useIsAdmin, useLogout } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';
import { BRAND } from '@/lib/brand';

interface NavItem {
  href: Route;
  label: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { href: '/app', label: 'Обзор', icon: LayoutDashboard },
  { href: '/app/keys', label: 'Ключи', icon: Key },
  { href: '/app/traces' as Route, label: 'Запросы', icon: Activity },
  { href: '/app/analytics' as Route, label: 'Аналитика', icon: BarChart3 },
  { href: '/app/usage', label: 'Расход', icon: BarChart3 },
  { href: '/app/billing', label: 'Биллинг', icon: CreditCard },
  { href: '/app/team', label: 'Команда', icon: Users },
  { href: '/app/settings', label: 'Настройки', icon: Settings },
];

// Админские nav-item'ы (Sprint 13.7+) — добавляются в конец списка только если
// useIsAdmin() возвращает true. Email пользователя должен быть в ADMIN_EMAILS
// env-списке gateway. Обычные клиенты эти пункты не видят.
//
// UX: вместо одного «Админка» с подменю — плоский список из 2 пунктов. Подменю
// добавляет лишний клик и для соло-CEO с двумя страницами это over-engineering.
// Заголовок-разделитель «Админ» визуально отделяет от пользовательских пунктов.
const ADMIN_NAV: NavItem[] = [
  { href: '/app/status' as Route, label: 'Статус', icon: Shield },
  { href: '/app/admin/balances' as Route, label: 'Балансы', icon: Wallet },
];

const MOBILE_NAV: NavItem[] = [
  { href: '/app', label: 'Обзор', icon: LayoutDashboard },
  { href: '/app/keys', label: 'Ключи', icon: Key },
  { href: '/app/usage', label: 'Расход', icon: BarChart3 },
  { href: '/app/billing', label: 'Биллинг', icon: CreditCard },
  { href: '/app/settings', label: 'Ещё', icon: MoreHorizontal },
];

interface Props {
  children: React.ReactNode;
}

export function DashboardShell({ children }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, toggle: toggleTheme } = useTheme();
  const { data: account } = useAccount();
  const { data: balance, isLoading: balanceLoading } = useBalance();
  const { data: adminCheck } = useIsAdmin();
  const logout = useLogout();
  const navItems = adminCheck?.is_admin ? [...NAV, ...ADMIN_NAV] : NAV;
  const [menuOpen, setMenuOpen] = useState(false);
  const userBlockRef = useRef<HTMLDivElement>(null);

  // ESC + click-outside для user-menu
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (!userBlockRef.current) return;
      if (!userBlockRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onClick);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onClick);
    };
  }, [menuOpen]);

  const handleLogout = async () => {
    setMenuOpen(false);
    try {
      await logout.mutateAsync();
    } finally {
      router.push('/login');
    }
  };

  const initials = account
    ? (account.name?.trim() || account.email).charAt(0).toUpperCase()
    : '·';

  return (
    <div className="brikko-marketing brikko-app-root">
      <a
        href="#app-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:bg-fg-primary focus:px-4 focus:py-2 focus:text-bg-base"
      >
        Перейти к содержимому
      </a>

      <div className="brikko-app-shell">
        <aside className="brikko-app-sidebar" aria-label="Навигация по дашборду">
          <Link href="/app" className="brikko-app-brand">
            {BRAND.name}
          </Link>

          <nav>
            <ul className="brikko-app-nav">
              {navItems.map((item) => {
                const active =
                  item.href === '/app'
                    ? pathname === '/app'
                    : pathname?.startsWith(item.href) ?? false;
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      className="brikko-app-nav-link"
                    >
                      <Icon
                        className="brikko-app-nav-icon"
                        strokeWidth={1.25}
                        aria-hidden="true"
                      />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="brikko-app-sidebar-foot">
            <Link
              href="/app/billing"
              aria-label="Текущий баланс — открыть биллинг"
              className="brikko-app-balance-pill"
              data-testid="topbar-balance"
            >
              <span className="brikko-app-balance-label">Баланс</span>
              <span>
                {balanceLoading
                  ? '…'
                  : balance
                    ? formatKopecks(balance.balance_kopecks, { forceFraction: true })
                    : '—'}
              </span>
            </Link>

            <div ref={userBlockRef} className="relative">
              <button
                type="button"
                className="brikko-app-user-row"
                onClick={() => setMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label="Меню пользователя"
              >
                <span className="brikko-app-user-avatar" aria-hidden="true">
                  {initials}
                </span>
                <span className="brikko-app-user-meta">
                  <span className="brikko-app-user-name">
                    {account?.name?.trim() || 'Пользователь'}
                  </span>
                  <span className="brikko-app-user-email">{account?.email ?? '—'}</span>
                </span>
                <ThemeIconButton theme={theme} onClick={(e) => { e.stopPropagation(); toggleTheme(); }} />
              </button>

              {menuOpen ? (
                <div role="menu" className="brikko-app-user-menu">
                  <Link
                    href="/app/settings"
                    role="menuitem"
                    className="brikko-app-user-menu-item"
                    onClick={() => setMenuOpen(false)}
                  >
                    <Settings size={14} strokeWidth={1.25} aria-hidden="true" />
                    Настройки
                  </Link>
                  <Link
                    href={'/' as Route}
                    role="menuitem"
                    className="brikko-app-user-menu-item"
                    onClick={() => setMenuOpen(false)}
                  >
                    <ExternalLink size={14} strokeWidth={1.25} aria-hidden="true" />
                    На сайт
                  </Link>
                  <button
                    type="button"
                    role="menuitem"
                    className="brikko-app-user-menu-item"
                    onClick={handleLogout}
                  >
                    <LogOut size={14} strokeWidth={1.25} aria-hidden="true" />
                    Выйти
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </aside>

        <div className="brikko-app-main">
          <header className="brikko-app-topbar">
            <Link href="/app" className="brikko-app-topbar-mobile-brand">
              {BRAND.name}
            </Link>
            <span className="brikko-app-topbar-context hidden lg:block">
              {account?.name ?? account?.email ?? 'Личный кабинет'}
            </span>
            <button
              type="button"
              aria-label={`Переключить на ${theme === 'light' ? 'тёмную' : 'светлую'} тему`}
              onClick={toggleTheme}
              className="brikko-theme-toggle lg:hidden"
            >
              {theme === 'light' ? <MoonIcon /> : <SunIcon />}
            </button>
          </header>

          <main id="app-main" className="brikko-app-content">
            {children}
          </main>
        </div>
      </div>

      <nav aria-label="Мобильная навигация" className="brikko-app-mobile-nav">
        {MOBILE_NAV.map((item) => {
          const active =
            item.href === '/app'
              ? pathname === '/app'
              : pathname?.startsWith(item.href) ?? false;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className="brikko-app-mobile-nav-link"
            >
              <Icon
                className="brikko-app-mobile-nav-icon"
                strokeWidth={1.25}
                aria-hidden="true"
              />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

function ThemeIconButton({
  theme,
  onClick,
}: {
  theme: 'light' | 'dark';
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick(e as unknown as React.MouseEvent);
        }
      }}
      aria-label={`Переключить на ${theme === 'light' ? 'тёмную' : 'светлую'} тему`}
      className="brikko-theme-toggle"
      style={{ width: 32, height: 32 }}
    >
      {theme === 'light' ? <MoonIcon /> : <SunIcon />}
    </span>
  );
}

function SunIcon() {
  return (
    <svg
      width="16"
      height="16"
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
      width="16"
      height="16"
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
