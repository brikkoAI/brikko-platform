import { MarketingShell } from '@/components/marketing/MarketingShell';

/**
 * Marketing layout (Sprint 13 — Cream Studio v6 rebrand, 2026-05-02).
 *
 * Все публичные страницы (главная, /pricing, /models, /docs, /faq, /playground,
 * /integrations, /cookbook, /legal/*, /status) оборачиваются единым
 * MarketingShell:
 *   - fixed top-bar (Brikko brand + hamburger overlay + theme toggle + Войти);
 *   - canvas particle background (TrafficCanvas);
 *   - shared minimal Footer;
 *   - theme toggle с persist через localStorage + system fallback.
 *
 * Прежняя индиго-палитра / sticky header заменены на strict grayscale
 * (см. globals.css `:root` + brikko-marketing.css).
 *
 * UX-обоснование: один shell → консистентность. Раньше /pricing, /models, /
 * имели разные шапки и спокойные индиго-акценты, теперь — единая cream-эстетика.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="brikko-marketing">
      <MarketingShell>{children}</MarketingShell>
    </div>
  );
}
