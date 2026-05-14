'use client';

/**
 * Top-fixed 2px line, scaleX 0→1 во время скролла.
 *
 * Lightweight: один scroll listener, без RAF (passive=true достаточно
 * для 2px полосы). Использует transform: scaleX чтобы избежать layout.
 */

import { useEffect, useState } from 'react';
import styles from './ReadingProgress.module.css';

export function ReadingProgress() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const onScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      if (max <= 0) {
        setProgress(0);
        return;
      }
      setProgress(Math.min(1, Math.max(0, window.scrollY / max)));
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    onScroll();

    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  return (
    <div
      className={styles.progress}
      style={{ transform: `scaleX(${progress})` }}
      aria-hidden="true"
    />
  );
}
