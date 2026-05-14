import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { DashboardShell } from '@/components/dashboard/DashboardShell';
import { ClosureBanner } from '@/components/layout/ClosureBanner';

// Authenticated dashboard не индексируется. Без cookie страница 307'ит на /login,
// что Googlebot интерпретировал как "Page with redirect" в GSC. noindex закрывает
// и сам /app, и все child-маршруты (/app/keys, /app/usage, /app/billing, …).
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

/**
 * Authenticated app shell. Async server-component, делает presence-check
 * cookie `vlt_access` ДО рендера child-страниц.
 *
 * Контракт:
 *   1. Нет cookie → redirect на /login?reason=session_required (без flash unauthed UI).
 *   2. Cookie есть → рендерим shell. Если cookie expired/невалидна — клиентский
 *      api.ts получит 401 на первом /account fetch'е и сделает refresh-flow либо
 *      redirect-to-login. Это компромисс: дополнительная server-side валидация
 *      JWT (проверка signature) добавила бы 30-50ms к каждому app-рендеру и
 *      потребовала бы шарить JWT_SECRET с web-контейнером — overkill для MVP.
 *
 * Sprint 13.6 (2026-05-02) — Cream Studio v6 dashboard rebrand:
 *   Sidebar/Topbar/MobileNav заменены на единый <DashboardShell>, который
 *   match'ит marketing/auth по тёмному/светлому переключателю и
 *   grayscale-палитре. Старые Tailwind-классы `bg-brand-*` / `text-brand-*`
 *   теперь автоматически переаливают на cream-tokens через remap в
 *   globals.css → не пришлось переписывать каждый файл.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = cookies();
  const accessCookie = cookieStore.get('vlt_access');
  if (!accessCookie?.value) {
    redirect('/login?reason=session_required');
  }

  return (
    <DashboardShell>
      <ClosureBanner />
      {children}
    </DashboardShell>
  );
}
