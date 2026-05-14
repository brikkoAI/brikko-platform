import { DocsLayout } from '@/components/docs/DocsLayout';

/**
 * /docs/* layout — оборачивает все страницы документации в двухколоночный
 * layout с sticky sidebar.
 *
 * Иерархия:
 *   <html>
 *     <body>
 *       <MarketingShell>  ← topbar + canvas + footer (apps/web/.../(marketing)/layout.tsx)
 *         <DocsLayout>     ← sidebar + main (этот файл)
 *           <page>         ← content конкретной /docs/* страницы
 *
 * UX-обоснование размещения:
 *   - Layout-segment Next.js 14 → не делает re-render sidebar при переходах
 *     между /docs/api/* (быстрая навигация). Альтернатива (импортировать
 *     DocsLayout в каждой page.tsx) — тормозила бы из-за повторного render'а
 *     sidebar.
 *   - Существующая `apps/web/src/app/(marketing)/docs/page.tsx` тоже
 *     проходит через этот layout, что ОЧЕНЬ хорошо: hub-страница теперь
 *     внутри той же навигации, что и подстраницы. Раньше она «висела»
 *     без TOC.
 */
export default function DocsRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <DocsLayout>{children}</DocsLayout>;
}
