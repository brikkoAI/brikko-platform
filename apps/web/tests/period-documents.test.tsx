/**
 * PeriodDocuments (Sprint 4 / Поток O): Comply Pack period-level downloads.
 *
 * Что покрываем:
 *   - При submit'е invoice-формы открывается /v1/billing/documents/invoice?period=YYYY-MM.
 *   - При submit'е summary-формы — /v1/billing/documents/summary?from=...&to=...
 *   - URL'ы — те, что согласованы с Поток M в docstring billingApi.invoiceUrl/summaryUrl.
 *   - Пустые/инвертированные диапазоны не дёргают window.open (защита бухгалтера от 400).
 *
 * Mock-стратегия:
 *   - Перехватываем window.open spy'ом — это main side-effect, проверяем URL контракт.
 *   - billingApi не мокаем — он чисто-вычислимый, тест ловит контракт API URL.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PeriodDocuments } from '@/components/dashboard/PeriodDocuments';

describe('<PeriodDocuments> Comply Pack downloads', () => {
  // typeof window.open is overloaded; используем any-cast только в локальной переменной spy.
  let openSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    openSpy = vi.fn(() => null);
    // window.open — overloaded type; vi.spyOn даёт MockInstance с конкретной сигнатурой.
    // Ставим mock через прямое присваивание, чтобы избежать infer-mismatch (тест-only код).
    Object.defineProperty(window, 'open', { value: openSpy, writable: true, configurable: true });
  });

  it('Скачать счёт за месяц → window.open с invoice URL и текущим месяцем', async () => {
    render(<PeriodDocuments />);

    await userEvent.click(screen.getByTestId('download-invoice'));

    expect(openSpy).toHaveBeenCalledTimes(1);
    const [url, target, features] = openSpy.mock.calls[0]!;
    expect(typeof url).toBe('string');
    expect(url as string).toMatch(/\/billing\/documents\/invoice\?period=\d{4}-\d{2}$/);
    expect(target).toBe('_blank');
    expect(features).toBe('noopener,noreferrer');
  });

  it('Скачать сводный отчёт → window.open с summary URL + from/to', async () => {
    render(<PeriodDocuments />);

    await userEvent.click(screen.getByTestId('download-summary'));

    expect(openSpy).toHaveBeenCalledTimes(1);
    const [url] = openSpy.mock.calls[0]!;
    expect(url as string).toMatch(
      /\/billing\/documents\/summary\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}$/,
    );
  });

  it('инвертированный диапазон (from > to) — window.open НЕ вызывается', async () => {
    render(<PeriodDocuments />);

    // Меняем порядок: ставим to раньше from.
    const fromInput = screen.getByLabelText('С какого числа') as HTMLInputElement;
    const toInput = screen.getByLabelText('По какое число') as HTMLInputElement;

    // Очистим и поставим инвертированный диапазон через fireEvent — userEvent.clear на date
    // input выдаёт пустую строку, что нативно не валидно, поэтому используем прямой setter.
    await userEvent.clear(fromInput);
    await userEvent.type(fromInput, '2026-04-30');
    await userEvent.clear(toInput);
    await userEvent.type(toInput, '2026-04-01');

    await userEvent.click(screen.getByTestId('download-summary'));
    // HTML5 form-validation отрубит при min/max-mismatch ИЛИ наша guard-проверка `from > to`.
    // Главное — не открыли левую URL.
    expect(openSpy).not.toHaveBeenCalled();
  });
});
