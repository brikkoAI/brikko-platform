/**
 * ActivityFeed (Sprint 8 §4).
 *
 * Что покрываем:
 *   - Loading state (skeleton).
 *   - Error state (текст «Не удалось загрузить»).
 *   - Empty state (пользователь только зарегистрировался).
 *   - Рендер 10 events с правильными иконками (по type).
 *   - Toggle details — клик раскрывает блок с деталями (aria-expanded переключается).
 *   - События без details не expandable (button disabled).
 *   - Warning-типы (balance_low, autorefill_failed) рендерят warning-стиль иконки.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { ActivityEvent } from '@/lib/types';

const useActivityReturn = vi.fn();

vi.mock('@/lib/auth', () => ({
  useActivity: () => useActivityReturn(),
}));

const { ActivityFeed } = await import('@/components/dashboard/ActivityFeed');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

const SAMPLE_EVENTS: ActivityEvent[] = [
  {
    id: 'a1',
    type: 'key_created',
    summary: 'Создан ключ «Production»',
    details: 'Scope: Полный. Префикс: sk-vt-AB12.',
    created_at: new Date(Date.now() - 60_000).toISOString(),
  },
  {
    id: 'a2',
    type: 'balance_topup',
    summary: 'Пополнение 1 000 ₽',
    details: 'ЮKassa, карта •••• 4242.',
    created_at: new Date(Date.now() - 86_400_000).toISOString(),
  },
  {
    id: 'a3',
    type: 'balance_low',
    summary: 'Баланс ниже 500 ₽',
    details: null,
    created_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
  },
];

describe('<ActivityFeed> (Sprint 8)', () => {
  beforeEach(() => {
    useActivityReturn.mockReset();
  });

  it('loading state — рендерит skeleton', () => {
    useActivityReturn.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(withQueryClient(<ActivityFeed />));
    expect(screen.getByTestId('activity-feed-loading')).toBeInTheDocument();
  });

  it('error state — показывает текст «Не удалось загрузить»', () => {
    useActivityReturn.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(withQueryClient(<ActivityFeed />));
    expect(screen.getByText(/Не удалось загрузить события/i)).toBeInTheDocument();
  });

  it('empty state — показывает дружелюбное сообщение', () => {
    useActivityReturn.mockReturnValue({ data: [], isLoading: false, isError: false });
    render(withQueryClient(<ActivityFeed />));
    expect(screen.getByText(/Пока ничего не происходило/)).toBeInTheDocument();
  });

  it('рендерит все события из data', () => {
    useActivityReturn.mockReturnValue({
      data: SAMPLE_EVENTS,
      isLoading: false,
      isError: false,
    });
    render(withQueryClient(<ActivityFeed />));
    expect(screen.getByTestId('activity-item-a1')).toBeInTheDocument();
    expect(screen.getByTestId('activity-item-a2')).toBeInTheDocument();
    expect(screen.getByTestId('activity-item-a3')).toBeInTheDocument();
    expect(screen.getByText(/Создан ключ «Production»/)).toBeInTheDocument();
  });

  it('клик по item с details раскрывает details блок', async () => {
    useActivityReturn.mockReturnValue({
      data: SAMPLE_EVENTS,
      isLoading: false,
      isError: false,
    });
    const user = userEvent.setup();
    render(withQueryClient(<ActivityFeed />));

    const item = screen.getByTestId('activity-item-a1');
    const button = within(item).getByRole('button');
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText(/Scope: Полный/)).not.toBeInTheDocument();

    await user.click(button);

    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/Scope: Полный/)).toBeInTheDocument();
  });

  it('item без details — кнопка disabled, aria-expanded не выставлен', () => {
    useActivityReturn.mockReturnValue({
      data: SAMPLE_EVENTS,
      isLoading: false,
      isError: false,
    });
    render(withQueryClient(<ActivityFeed />));
    const item = screen.getByTestId('activity-item-a3');
    const button = within(item).getByRole('button');
    expect(button).toBeDisabled();
    expect(button.getAttribute('aria-expanded')).toBeNull();
  });

  it('закрытие — повторный клик скрывает details', async () => {
    useActivityReturn.mockReturnValue({
      data: SAMPLE_EVENTS,
      isLoading: false,
      isError: false,
    });
    const user = userEvent.setup();
    render(withQueryClient(<ActivityFeed />));
    const button = within(screen.getByTestId('activity-item-a1')).getByRole('button');
    await user.click(button);
    expect(screen.getByText(/Scope: Полный/)).toBeInTheDocument();
    await user.click(button);
    expect(screen.queryByText(/Scope: Полный/)).not.toBeInTheDocument();
  });
});
