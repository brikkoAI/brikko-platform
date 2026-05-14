import type { Metadata } from 'next';
import { MarketingShell } from '@/components/marketing/MarketingShell';

/**
 * Demo route layout (Sprint 13.8 landing refresh, 2026-05-09).
 *
 * `/demo/*` страницы (traces, analytics, status) — публичные full-screen
 * демонстрации UI кабинета с фикстурными данными. Не требуют auth.
 *
 * Решение по shell:
 *   - НЕ используем DashboardShell — нет sidebar'а, нет /app роуты, нет
 *     useSession-overhead. Это marketing-territory.
 *   - Используем MarketingShell — даёт top-bar (Brikko + theme toggle +
 *     login), canvas-фон трафика, footer. Получаем консистентность с
 *     лендингом за «бесплатно».
 *
 * Робот-индексация: `noindex` на demo-страницах. Они не должны конкурировать
 * в SERP с /app/* — там реальный продукт; и не должны индексироваться как
 * фикстура (риск misleading-результатов).
 */

export const metadata: Metadata = {
  title: 'Демо BrikkoLens',
  description:
    'Пример того, как выглядит панель управления у клиентов Brikko: трейсы запросов, аналитика расходов и статус провайдеров.',
  robots: { index: false, follow: false },
};

export default function DemoLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="brikko-marketing">
      <MarketingShell>{children}</MarketingShell>
    </div>
  );
}
