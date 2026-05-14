'use client';

/**
 * DocsSidebar — sticky табличка содержимого слева на /docs/* страницах.
 *
 * UX-обоснование (Cream Studio v6 + Stripe/Linear docs pattern):
 *   - sticky top — пользователь всегда видит wayfinder при скролле reference-
 *     страниц (которые часто длинные). Sticky на desktop, native <details>
 *     на mobile (zero-JS, accessible, keyboard-friendly).
 *   - Активный пункт подсвечен через usePathname() — closure principle, NN/g.
 *     Без подсветки пользователь теряется в длинном списке (15+ пунктов).
 *   - Группы (Начало / API / CLI / Концепции / Интеграции / Compliance) —
 *     не collapsible. Принцип «всё видно сразу» работает лучше для reference-
 *     доки, чем accordion: cognitive load на «угадать где спрятано» >
 *     cognitive load от длинного списка (Stripe, Vercel, Linear делают так).
 *   - «Coming soon» — приглушённые pill, не disabled (cursor not-allowed). Это
 *     честно — пользователь видит roadmap, кликабельность сохраняется (страница-
 *     заглушка с ETA). Лучше, чем спрятать или сделать non-interactive.
 *   - Ширина 240px (industry default для doc-sidebar). Меньше — обрезаются
 *     длинные labels. Больше — съедает контент.
 *   - Никаких иконок. Reference-доки — это слова, иконки только шумят.
 *
 * Mobile (<768px):
 *   - <details> с label «Содержание» в header'е main-area. Свёрнут по дефолту,
 *     открывается тапом, закрывается тапом по любому пункту (нативное
 *     поведение). Никакого hamburger / drawer — overkill для линейного списка.
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { Route } from 'next';
import { DOCS_NAV } from './nav';

export function DocsSidebar() {
  const pathname = usePathname() ?? '/docs';

  return (
    <nav aria-label="Содержание документации" className="brikko-docs-sidebar">
      {DOCS_NAV.map((group) => (
        <div key={group.title} className="brikko-docs-sidebar-group">
          <p className="brikko-docs-sidebar-title">{group.title}</p>
          <ul className="brikko-docs-sidebar-list">
            {group.links.map((link) => {
              const active = pathname === link.href;
              return (
                <li key={link.href}>
                  <Link
                    href={link.href as Route}
                    className={
                      active
                        ? 'brikko-docs-sidebar-link brikko-docs-sidebar-link--active'
                        : 'brikko-docs-sidebar-link'
                    }
                    aria-current={active ? 'page' : undefined}
                  >
                    <span>{link.label}</span>
                    {link.comingSoon ? (
                      <span className="brikko-docs-sidebar-tag">soon</span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
