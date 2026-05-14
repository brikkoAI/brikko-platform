'use client';

/**
 * Sticky cursor-follow dot.
 *
 * Disabled на touch (matchMedia('(hover: hover) and (pointer: fine)')) и
 * при prefers-reduced-motion. На hover button/a — увеличивается + slight blur.
 *
 * Implementation: один fixed div, RAF-loop с lerp 0.12. Hover-detection
 * через delegated mouseover/mouseout на document — дешевле, чем
 * querySelectorAll каждый кадр.
 */

import { useEffect, useRef } from 'react';
import styles from './CursorDot.module.css';

const LERP = 0.12;

export function CursorDot() {
  const dotRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const fineHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!fineHover || reduced) return;

    const dot = dotRef.current;
    if (!dot) return;

    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;
    let dotX = mouseX;
    let dotY = mouseY;
    let rafId = 0;
    let visible = false;

    const onMove = (e: PointerEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      if (!visible) {
        visible = true;
        dot.dataset.visible = 'true';
      }
    };

    const onLeave = () => {
      visible = false;
      dot.dataset.visible = 'false';
    };

    const onOver = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      const interactive = target?.closest('button, a, [role="button"], input, textarea, select');
      dot.dataset.hover = interactive ? 'true' : 'false';
    };

    const tick = () => {
      dotX += (mouseX - dotX) * LERP;
      dotY += (mouseY - dotY) * LERP;
      dot.style.transform = `translate3d(${dotX - 4}px, ${dotY - 4}px, 0)`;
      rafId = requestAnimationFrame(tick);
    };

    window.addEventListener('pointermove', onMove);
    document.addEventListener('mouseover', onOver);
    document.addEventListener('mouseleave', onLeave);
    rafId = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener('pointermove', onMove);
      document.removeEventListener('mouseover', onOver);
      document.removeEventListener('mouseleave', onLeave);
      cancelAnimationFrame(rafId);
    };
  }, []);

  return <div ref={dotRef} className={styles.dot} aria-hidden="true" data-visible="false" />;
}
