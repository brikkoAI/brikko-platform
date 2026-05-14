import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Kopecks } from './types';

/** Tailwind class merger — конкатенирует и резолвит конфликты utility-классов. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// ICU нестабилен между версиями Node по поводу пробела-разделителя для `ru-RU`:
//   - до ICU 72 — regular NBSP (U+00A0)
//   - ICU 72+   — narrow NBSP (U+202F)
// Оба невидимы для пользователя, но создают flaky-тесты, флаки скриншот-сравнений и
// копипасты. Нормализуем оба варианта в обычный пробел (U+0020) — это контракт
// формата наружу: чисто и стабильно. См. TD-026.
const ICU_NUMERIC_SPACES = /[  ]/g;

function normalizeIcuSpaces(value: string): string {
  return value.replace(ICU_NUMERIC_SPACES, ' ');
}

/** Форматирование рублей (плавающие RUB) — ru-RU, число<space>₽. */
export function formatRub(amount: number, fractionDigits = 0): string {
  const formatted = new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(amount);
  return normalizeIcuSpaces(`${formatted} ₽`);
}

/**
 * Форматирование суммы в копейках. По умолчанию 2 знака если есть «копейки», иначе целые.
 *
 * Принимает `Kopecks | 0` потому что компоненты часто пишут
 * `account.data?.balance_kopecks ?? 0` — литерал `0` это валидный денежный ноль,
 * не нужно его brand'овать через factory. Под капотом мы всё равно работаем с
 * `number`-ом.
 */
export function formatKopecks(
  amount: Kopecks | 0,
  opts: { forceFraction?: boolean } = {},
): string {
  const rubFloat = amount / 100;
  const hasFraction = amount % 100 !== 0;
  const fractionDigits = opts.forceFraction || hasFraction ? 2 : 0;
  // Знак сохраняем (для usage transactions) — отрицательные значения = списания.
  return formatRub(rubFloat, fractionDigits);
}

/** Форматирование числа токенов с разделителями разрядов. */
export function formatTokens(n: number): string {
  return normalizeIcuSpaces(new Intl.NumberFormat('ru-RU').format(n));
}

/** Маскирование API-ключа: показываем 7 первых и 4 последних символа. */
export function maskApiKey(key: string): string {
  if (key.length < 12) return '•••';
  return `${key.slice(0, 7)}…${key.slice(-4)}`;
}

/** ISO-дата → ru-RU «28 апр 2026». */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** ISO-дата → ru-RU «28 апр, 14:32». */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** «дней назад / сегодня» для last_used_at. */
export function formatRelative(iso: string | null): string {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  const day = 24 * 60 * 60 * 1000;
  if (diffMs < 60_000) return 'только что';
  if (diffMs < 60 * 60 * 1000) return `${Math.floor(diffMs / 60_000)} мин назад`;
  if (diffMs < day) return `${Math.floor(diffMs / (60 * 60 * 1000))} ч назад`;
  if (diffMs < 7 * day) return `${Math.floor(diffMs / day)} дн назад`;
  return formatDate(iso);
}
