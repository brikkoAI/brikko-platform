/**
 * TransactionsFilters + TransactionsActiveFilters + filtersToQuery (Sprint 8 §3).
 *
 * Что покрываем:
 *   - filtersToQuery: правильно конвертирует ₽ → копейки для min/max.
 *   - filtersToQuery: возвращает kind: undefined если nothing selected.
 *   - TransactionsActiveFilters: рендерит chips для всех активных фильтров.
 *   - TransactionsActiveFilters: «Сбросить всё» вызывает onChange(EMPTY_FILTERS).
 *   - Toggle kind через checkbox.
 *   - Search debounce 300ms — onChange зовётся с задержкой.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  TransactionsFilters,
  TransactionsActiveFilters,
  filtersToQuery,
  EMPTY_FILTERS,
  type TransactionsFilterValue,
} from '@/components/dashboard/TransactionsFilters';

describe('filtersToQuery (Sprint 8)', () => {
  it('пустые фильтры → только page params', () => {
    const q = filtersToQuery(EMPTY_FILTERS, { limit: 25, offset: 0 });
    expect(q).toEqual({
      from: undefined,
      to: undefined,
      kind: undefined,
      search: undefined,
      min_amount: undefined,
      max_amount: undefined,
      limit: 25,
      offset: 0,
    });
  });

  it('конвертирует minAmountRub/maxAmountRub в копейки', () => {
    const q = filtersToQuery(
      { ...EMPTY_FILTERS, minAmountRub: '500', maxAmountRub: '1000' },
      { limit: 25, offset: 0 },
    );
    expect(q.min_amount).toBe(50_000);
    expect(q.max_amount).toBe(100_000);
  });

  it('search.trim — пустые строки → undefined', () => {
    const q = filtersToQuery(
      { ...EMPTY_FILTERS, search: '   ' },
      { limit: 25, offset: 0 },
    );
    expect(q.search).toBeUndefined();
  });

  it('kinds non-empty array → передаётся в kind', () => {
    const q = filtersToQuery(
      { ...EMPTY_FILTERS, kinds: ['topup', 'refund'] },
      { limit: 25, offset: 0 },
    );
    expect(q.kind).toEqual(['topup', 'refund']);
  });

  it('передаёт fromDate / toDate как есть', () => {
    const q = filtersToQuery(
      { ...EMPTY_FILTERS, fromDate: '2026-04-01', toDate: '2026-04-30' },
      { limit: 25, offset: 0 },
    );
    expect(q.from).toBe('2026-04-01');
    expect(q.to).toBe('2026-04-30');
  });
});

describe('<TransactionsFilters>', () => {
  it('toggleKind: клик по checkbox добавляет/убирает тип', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    let value: TransactionsFilterValue = EMPTY_FILTERS;
    const { rerender } = render(
      <TransactionsFilters
        value={value}
        onChange={(v) => {
          value = v;
          onChange(v);
        }}
      />,
    );
    await user.click(screen.getByTestId('transactions-kind-topup'));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ kinds: ['topup'] }),
    );

    rerender(<TransactionsFilters value={value} onChange={onChange} />);
    await user.click(screen.getByTestId('transactions-kind-topup'));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ kinds: [] }),
    );
  });

  it('change по date range пушит обновлённое значение наверх', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TransactionsFilters value={EMPTY_FILTERS} onChange={onChange} />);

    const fromInput = screen.getByTestId('transactions-from');
    await user.type(fromInput, '2026-04-01');
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ fromDate: '2026-04-01' }),
    );
  });

  it('search — пушит наверх с debounce 300ms', async () => {
    // Используем real timers + waitFor — fake timers ломают setup() userEvent
    // в нашем CRA-стеке. waitFor проверяет состояние через ~50ms интервал.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TransactionsFilters value={EMPTY_FILTERS} onChange={onChange} />);

    await user.type(screen.getByTestId('transactions-search'), 'kassa');
    // Сразу после type — onChange ещё не вызван (debounce 300ms).
    expect(onChange).not.toHaveBeenCalled();

    // Через ~300ms onChange должен сработать с финальным значением.
    await new Promise((r) => setTimeout(r, 350));
    expect(onChange).toHaveBeenCalled();
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: 'kassa' }),
    );
  });
});

describe('<TransactionsActiveFilters>', () => {
  it('возвращает null когда нет активных фильтров', () => {
    const { container } = render(
      <TransactionsActiveFilters value={EMPTY_FILTERS} onChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('рендерит chip для search + кнопку Сбросить всё', () => {
    render(
      <TransactionsActiveFilters
        value={{ ...EMPTY_FILTERS, search: 'юкасса' }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('transactions-active-filters')).toBeInTheDocument();
    expect(screen.getByText(/Поиск: «юкасса»/)).toBeInTheDocument();
    expect(screen.getByTestId('transactions-clear-filters')).toBeInTheDocument();
  });

  it('рендерит отдельные chip для каждого выбранного kind', () => {
    render(
      <TransactionsActiveFilters
        value={{ ...EMPTY_FILTERS, kinds: ['topup', 'refund'] }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/Тип: Пополнения/)).toBeInTheDocument();
    expect(screen.getByText(/Тип: Возвраты/)).toBeInTheDocument();
  });

  it('клик по X в chip убирает только этот фильтр', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TransactionsActiveFilters
        value={{ ...EMPTY_FILTERS, search: 'foo', kinds: ['topup'] }}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByLabelText(/Убрать фильтр: Поиск/));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: '', kinds: ['topup'] }),
    );
  });

  it('«Сбросить всё» вызывает onChange(EMPTY_FILTERS)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TransactionsActiveFilters
        value={{ ...EMPTY_FILTERS, search: 'foo' }}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByTestId('transactions-clear-filters'));
    expect(onChange).toHaveBeenCalledWith(EMPTY_FILTERS);
  });
});
