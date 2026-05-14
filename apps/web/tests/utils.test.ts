import { describe, expect, it } from 'vitest';
import {
  cn,
  formatKopecks,
  formatRelative,
  formatRub,
  formatTokens,
  maskApiKey,
} from '@/lib/utils';
import type { Kopecks } from '@/lib/types';

describe('cn', () => {
  it('конкатенирует классы', () => {
    expect(cn('a', 'b')).toBe('a b');
  });
  it('пропускает falsy', () => {
    expect(cn('a', false, undefined, 'b')).toBe('a b');
  });
  it('резолвит конфликт tailwind: последний px-выигрывает', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });
});

describe('formatRub', () => {
  it('форматирует с разделителями разрядов и пробелом перед ₽', () => {
    expect(formatRub(12345)).toBe('12 345 ₽');
  });
  it('держит указанное число дробных', () => {
    expect(formatRub(99.5, 2)).toBe('99,50 ₽');
  });
});

describe('formatKopecks', () => {
  it('целые рубли без копеек', () => {
    expect(formatKopecks(100_000 as Kopecks)).toBe('1 000 ₽');
  });
  it('копейки — два знака', () => {
    expect(formatKopecks(100_050 as Kopecks)).toBe('1 000,50 ₽');
  });
  it('forceFraction форсирует 2 знака', () => {
    expect(formatKopecks(100_000 as Kopecks, { forceFraction: true })).toBe('1 000,00 ₽');
  });
  it('сохраняет знак для отрицательных списаний', () => {
    expect(formatKopecks(-20_000 as Kopecks)).toBe('-200 ₽');
  });
});

describe('formatTokens', () => {
  it('разделители разрядов ru-RU', () => {
    expect(formatTokens(1_234_567)).toBe('1 234 567');
  });
});

describe('maskApiKey', () => {
  it('маскирует длинный ключ показывая префикс и суффикс', () => {
    expect(maskApiKey('sk-vt-abcdef1234567890xyz')).toBe('sk-vt-a…0xyz');
  });
  it('возвращает заглушку для слишком короткого', () => {
    expect(maskApiKey('short')).toBe('•••');
  });
});

describe('formatRelative', () => {
  it('null → "—"', () => {
    expect(formatRelative(null)).toBe('—');
  });
  it('менее минуты — "только что"', () => {
    expect(formatRelative(new Date().toISOString())).toBe('только что');
  });
  it('час назад — "1 ч назад"', () => {
    const iso = new Date(Date.now() - 60 * 60 * 1000 - 5_000).toISOString();
    expect(formatRelative(iso)).toMatch(/ч назад/);
  });
});
