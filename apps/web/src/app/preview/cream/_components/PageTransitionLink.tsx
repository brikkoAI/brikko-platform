'use client';

/**
 * Page-transition orchestrator (Feature 6, safe variant).
 *
 * Полный SPA-transition в Next.js App Router нетривиален из-за того, что
 * suspense boundary нового route'а монтируется параллельно со старым —
 * чистый fade-out + router.push гонит race-condition на Lenis-state и
 * scroll-restoration. Поэтому делаем сейчас **safe-вариант** из спеки:
 *
 *   * Anchor-links (#proof и т.п.) → preventDefault + Lenis.scrollTo с
 *     длинной duration → ощущение SPA-навигации.
 *   * Full route changes (/signup, /playground) → fade-out body 200ms,
 *     потом нативный navigate. Браузер всё равно сделает hard-load, но
 *     пользователь видит "оборванный" current view, а не белую вспышку.
 *
 * Когда лендинг переедет в полный SPA-shell — заменим на router.push +
 * useTransition + custom <Suspense fallback>.
 */

import { useEffect } from 'react';

type LenisLike = {
  scrollTo: (target: HTMLElement | number, opts?: { duration?: number; offset?: number }) => void;
};

declare global {
  interface Window {
    __brikkoLenis?: LenisLike;
  }
}

export function PageTransitionOrchestrator() {
  useEffect(() => {
    if (typeof window === 'undefined') return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const onClick = (e: MouseEvent) => {
      // Modifier-clicks → не трогаем (open in new tab).
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (e.button !== 0) return;

      const target = e.target as HTMLElement | null;
      const link = target?.closest('a');
      if (!link) return;

      const href = link.getAttribute('href');
      if (!href) return;
      if (link.target === '_blank') return;
      if (link.hasAttribute('download')) return;

      // External — leave it alone.
      if (/^https?:\/\//.test(href) && !href.includes(window.location.host)) return;
      if (href.startsWith('mailto:') || href.startsWith('tel:')) return;

      // Anchor on same page → smooth-scroll via Lenis.
      if (href.startsWith('#')) {
        const id = href.slice(1);
        if (!id) return;
        const el = document.getElementById(id);
        if (!el) return;
        e.preventDefault();
        const lenis = window.__brikkoLenis;
        if (lenis && !reduced) {
          lenis.scrollTo(el, { duration: 1.4, offset: -80 });
        } else {
          el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
        }
        return;
      }

      // Same-origin route change → fade-out, then native navigate.
      // (Native navigate потому что часть routes — server-rendered marketing
      // pages вне cream-shell; SPA-router.push не покрыл бы их.)
      if (href.startsWith('/') && !reduced) {
        e.preventDefault();
        document.documentElement.style.transition = 'opacity 200ms ease, filter 200ms ease';
        document.documentElement.style.opacity = '0';
        document.documentElement.style.filter = 'blur(2px)';
        window.setTimeout(() => {
          window.location.href = href;
        }, 200);
      }
    };

    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);

  return null;
}
