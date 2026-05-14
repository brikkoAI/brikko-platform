'use client';

import { HelpCircle } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface HelperTooltipProps {
  /** Текст внутри popover'а. Максимум 2 строки по копирайт-гайду. */
  content: ReactNode;
  /** Кастомное aria-label кнопки (default: «Подсказка»). */
  label?: string;
  className?: string;
}

/**
 * Lightweight (?) tooltip. Не тащим radix-tooltip — экономим ~12 КБ JS на бандл.
 *
 * UX-обоснование:
 *   - Состояние tracked через 2 boolean (hovered / focused). Open = hovered || focused || clicked.
 *     Это решает кейс «click при уже-hovered» — без двойного toggle close после tap'а.
 *   - role="tooltip" + aria-describedby — screen reader зачитает helper text при focus'е.
 *   - Ширина 240 пх — достаточно для 2 строк русского текста и не уезжает за вьюпорт
 *     в мобильной форме.
 */
export function HelperTooltip({ content, label = 'Подсказка', className }: HelperTooltipProps) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [clicked, setClicked] = useState(false);
  const open = hovered || focused || clicked;

  return (
    <span className={cn('relative inline-flex', className)}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-gray-400 hover:text-gray-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          // Click triggers focus → blur cycle (touch). Без сброса clicked
          // tooltip останется открыт даже после blur'а.
          setClicked(false);
        }}
        onClick={() => setClicked((v) => !v)}
      >
        <HelpCircle className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open ? (
        <span
          id={id}
          role="tooltip"
          className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 w-60 -translate-x-1/2 rounded-md border border-gray-200 bg-white px-3 py-2 text-body-sm text-gray-700 shadow-md"
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
