'use client';

/**
 * useTheme — общий hook для marketing- и auth-shell'ов (Sprint 13.5).
 *
 * Контракт:
 *   - Источник истины — `localStorage['brikko-theme']` (значения: `light` | `dark`).
 *   - Если в storage пусто — fallback на `prefers-color-scheme: dark`.
 *   - Применяет `[data-theme]` на `<html>`, чтобы CSS-vars работали глобально.
 *   - Слушает system-level смену темы только пока юзер не выбрал свою (нет
 *     записи в localStorage). Это match'ит поведение macOS Safari / Linear /
 *     Stripe — system preference не должен перетирать осознанный выбор.
 *
 * SSR-safe: при первом рендере возвращаем `light` + `themeReady=false`.
 *   Никакие side-effect'ы до hydration не запускаются. Когда `themeReady=true`,
 *   shell может безопасно рендерить иконку текущей темы без hydration mismatch'а.
 *
 * Edge-cases:
 *   - localStorage недоступен (private mode / quota / sandboxed iframe) —
 *     hook деградирует до in-memory state. Тема всё равно работает в текущей
 *     сессии, просто не персистится.
 *   - `prefers-color-scheme: dark` matchMedia может отсутствовать в очень старых
 *     браузерах — в этом случае мы остаёмся на `light` (безопасный default).
 */

import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'brikko-theme';

function readStoredTheme(): Theme | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === 'light' || raw === 'dark' ? raw : null;
  } catch {
    return null;
  }
}

function writeStoredTheme(theme: Theme): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // private mode / sandboxed — silently игнорируем.
  }
}

function detectSystemTheme(): Theme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'light';
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export interface UseThemeResult {
  theme: Theme;
  ready: boolean;
  toggle: () => void;
  set: (next: Theme) => void;
}

export function useTheme(): UseThemeResult {
  const [theme, setTheme] = useState<Theme>('light');
  const [ready, setReady] = useState(false);

  // Hydrate один раз на mount.
  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored ?? detectSystemTheme());
    setReady(true);

    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }

    // System-theme listener: меняем тему ТОЛЬКО если юзер не выбрал свою.
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onSystemChange = (e: MediaQueryListEvent) => {
      const userStored = readStoredTheme();
      if (!userStored) setTheme(e.matches ? 'dark' : 'light');
    };
    mq.addEventListener('change', onSystemChange);
    return () => mq.removeEventListener('change', onSystemChange);
  }, []);

  // Apply на <html> + persist.
  useEffect(() => {
    if (!ready) return;
    document.documentElement.setAttribute('data-theme', theme);
    writeStoredTheme(theme);
  }, [theme, ready]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'light' ? 'dark' : 'light'));
  }, []);

  const set = useCallback((next: Theme) => setTheme(next), []);

  return { theme, ready, toggle, set };
}
