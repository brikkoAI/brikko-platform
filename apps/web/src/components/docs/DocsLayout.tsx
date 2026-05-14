/**
 * DocsLayout — двухколоночный layout для всех /docs/* страниц.
 *
 * Структура (desktop ≥1024px):
 *   ┌────────────┬────────────────────────────────┐
 *   │ sidebar    │ main content                   │
 *   │ (sticky)   │ (max-width 760px, центр)       │
 *   │ 240px      │                                │
 *   └────────────┴────────────────────────────────┘
 *
 * Mobile (<1024px): sidebar сворачивается в <details> сверху. Main занимает
 * 100% width. Padding контент-области уменьшен.
 *
 * UX-обоснование:
 *   - Двухколоночный layout — стандарт docs (Stripe, Vercel, Linear, Tailwind,
 *     Next.js). Пользователь привык — узнаваемость > креатив для reference-доки.
 *   - Sidebar sticky → wayfinder всегда виден при скролле. Без него длинная
 *     страница вынуждает скроллить вверх для навигации (NN/g принцип
 *     «consistent location»).
 *   - Контент max-width 760px — оптимальный line-length для технического
 *     текста (60-75 chars/line по Bringhurst). Существующая `max-w-3xl` в
 *     /docs/page.tsx даёт примерно это же.
 *   - Никаких right-rail TOC (как у Stripe) в MVP — over-engineering для
 *     наших страниц длиной 1000-1500 слов. Anchor-навигация внутри страницы
 *     (как сейчас в /docs/smart-routing) достаточна.
 */

import { DocsSidebar } from './DocsSidebar';

interface Props {
  children: React.ReactNode;
}

export function DocsLayout({ children }: Props) {
  return (
    <div className="brikko-docs-shell">
      {/* Mobile: <details> с sidebar внутри. На desktop скрыто через CSS. */}
      <details className="brikko-docs-mobile-toc">
        <summary className="brikko-docs-mobile-toc-summary">
          <span>Содержание документации</span>
          <span aria-hidden="true" className="brikko-docs-mobile-toc-icon">
            +
          </span>
        </summary>
        <div className="brikko-docs-mobile-toc-body">
          <DocsSidebar />
        </div>
      </details>

      <aside className="brikko-docs-sidebar-wrap">
        <DocsSidebar />
      </aside>

      <main className="brikko-docs-main">{children}</main>
    </div>
  );
}
