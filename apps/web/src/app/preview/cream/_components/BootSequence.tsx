'use client';

/**
 * 800ms boot overlay — "B" → "Brikko" letter-stagger fade.
 *
 * Скрывает chaotic-startup canvas/typewriter в первые 0.8s. После первого
 * визита (localStorage.brikko_visited) — skip целиком. prefers-reduced-motion
 * — тоже skip. Не блокирует SSR/hydration: render через useEffect-mount,
 * removeFromDOM через setState после 800ms.
 *
 * Sequence:
 *   0ms     overlay shown, "B" 180px serif
 *   200ms   morph: реальная letter-by-letter fade-in "rikko" (stagger 60ms)
 *   500ms   overlay начинает opacity 1→0 fadeout (300ms)
 *   800ms   removeFromDOM
 */

import { useEffect, useState } from 'react';
import styles from './BootSequence.module.css';

const REMAINING_LETTERS = ['r', 'i', 'k', 'k', 'o'];

export function BootSequence() {
  const [stage, setStage] = useState<'mount' | 'expand' | 'fade' | 'gone'>('mount');

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const visited = window.localStorage.getItem('brikko_visited') === '1';

    if (reduced || visited) {
      setStage('gone');
      // Mark visited even if we skipped — repeated visits within session
      // shouldn't try to re-init the boot screen.
      try {
        window.localStorage.setItem('brikko_visited', '1');
      } catch {
        /* private mode — fine, fall back to per-session memory */
      }
      return;
    }

    const t1 = window.setTimeout(() => setStage('expand'), 200);
    const t2 = window.setTimeout(() => setStage('fade'), 500);
    const t3 = window.setTimeout(() => {
      setStage('gone');
      try {
        window.localStorage.setItem('brikko_visited', '1');
      } catch {
        /* private mode */
      }
    }, 800);

    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
    };
  }, []);

  if (stage === 'gone') return null;

  return (
    <div
      className={`${styles.overlay} ${stage === 'fade' ? styles.fadeOut : ''}`}
      aria-hidden="true"
    >
      <div className={styles.logo}>
        <span className={styles.letterPrimary}>B</span>
        {stage !== 'mount' && (
          <span className={styles.tail}>
            {REMAINING_LETTERS.map((letter, i) => (
              <span
                key={i}
                className={styles.tailLetter}
                style={{ animationDelay: `${i * 60}ms` }}
              >
                {letter}
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}
