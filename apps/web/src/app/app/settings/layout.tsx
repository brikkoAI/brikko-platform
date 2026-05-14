'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

/**
 * Settings shell с табами. Один layout на все sub-routes (/settings/*) — это даёт
 * persistent-навигацию и не дёргает useAccount() при каждой смене таба.
 *
 * UX-обоснование:
 *  - Горизонтальные tabs наверху (NOT sidebar): на десктопе у нас уже есть глобальный
 *    Sidebar, второй вертикальный был бы «двойная навигация» — антипаттерн.
 *  - На мобилке tabs скроллятся горизонтально (overflow-x-auto). Не collapse в select —
 *    select на мобилке хуже UX, чем горизонтальный swipe.
 *  - Sprint 7: closure-banner перенесён в `app/layout.tsx` (общий dashboard layout),
 *    чтобы пользователь видел его на ЛЮБОМ маршруте /app/* — а не только в Settings.
 *    На /app/settings/security банер скрыт сам (см. ClosureBanner), потому что там
 *    CloseAccountCard уже показывает scheduled-state.
 */

interface TabItem {
  href: string;
  label: string;
  /**
   * Если задан hash — таб скроллит к секции на главной /settings (Privacy/Telegram —
   * исторически на одной странице, не дробим в Sprint 6, иначе ломаем 123 теста).
   */
  hash?: string;
}

const TABS: TabItem[] = [
  { href: '/app/settings', label: 'Профиль' },
  { href: '/app/settings/security', label: 'Безопасность' },
  { href: '/app/settings/routing', label: 'Routing' },
  { href: '/app/settings#privacy', label: 'Privacy', hash: 'privacy' },
  { href: '/app/settings#telegram', label: 'Telegram', hash: 'telegram' },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <header>
        <h1 className="text-3xl font-semibold text-gray-900">Настройки</h1>
        <p className="mt-2 text-body text-gray-500">
          Профиль, безопасность, приватность и уведомления.
        </p>
      </header>

      <nav
        aria-label="Разделы настроек"
        className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0"
        data-testid="settings-tabs"
      >
        <ul role="tablist" className="flex min-w-max gap-1 border-b border-gray-200">
          {TABS.map((tab) => {
            // Hash-табы (Privacy / Telegram) активны только на /settings — это ссылки на секции.
            const hrefPath = tab.href.split('#')[0];
            const isActive = tab.hash
              ? false
              : pathname === hrefPath;
            const slug = tab.hash ?? hrefPath?.split('/').pop() ?? 'profile';
            return (
              <li key={tab.href}>
                <Link
                  // Next 14 typed routes: hash-варианты (#privacy) валидны как href,
                  // но TS-router не знает про комбинации /path#hash. Cast — самый
                  // прагматичный путь без отключения typed-routes для всего проекта.
                  href={tab.href as never}
                  role="tab"
                  aria-selected={isActive}
                  aria-current={isActive ? 'page' : undefined}
                  className={cn(
                    'inline-block px-4 py-2.5 text-body-sm font-medium transition-colors',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600',
                    'border-b-2 -mb-px',
                    isActive
                      ? 'border-brand-600 text-brand-700'
                      : 'border-transparent text-gray-600 hover:text-gray-900',
                  )}
                  data-testid={`settings-tab-${slug}`}
                >
                  {tab.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div>{children}</div>
    </div>
  );
}
