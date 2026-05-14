/**
 * AutoRefillCard (Sprint 8 §1).
 *
 * Что покрываем:
 *   - Loading skeleton при первом запросе.
 *   - Disabled state — banner «Авто-пополнение выключено» + form для включения.
 *   - Enabled state — поля префилл'ятся текущими значениями + кнопка «Сохранить».
 *   - Failures ≥ 3 — error-banner с CTA «Обновить карту».
 *   - Нет saved_methods — warning banner «Сначала пополни баланс».
 *   - Validation: threshold > amount → ошибка inline.
 *   - Submit вызывает onSave с правильным kopecks-кастом.
 *   - Disable вызывает disable mutation.
 *
 * Стратегия мокинга — мокаем только хуки auth (как в других тестах).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { AutorefillState, Kopecks } from '@/lib/types';
import { toKopecks } from '@/lib/types';

// Toast — no-op.
vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const updateMock = vi.fn();
const disableMock = vi.fn();
const useAutorefillReturn = vi.fn();

vi.mock('@/lib/auth', () => ({
  useAutorefill: () => useAutorefillReturn(),
  useUpdateAutorefill: () => ({ mutateAsync: updateMock, isPending: false }),
  useDisableAutorefill: () => ({ mutateAsync: disableMock, isPending: false }),
}));

const { AutoRefillCard } = await import('@/components/dashboard/settings/AutoRefillCard');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

const baseState: AutorefillState = {
  enabled: false,
  threshold_kopecks: null,
  amount_kopecks: null,
  payment_method_id: null,
  saved_methods: [
    {
      id: 'pm-1',
      card_mask: '•••• 4242',
      brand: 'Visa',
      added_at: '2026-04-01T00:00:00Z',
      is_default: true,
    },
    {
      id: 'pm-2',
      card_mask: '•••• 1111',
      brand: 'MasterCard',
      added_at: '2026-04-15T00:00:00Z',
      is_default: false,
    },
  ],
  failure_count: 0,
};

describe('<AutoRefillCard> (Sprint 8)', () => {
  beforeEach(() => {
    updateMock.mockReset();
    disableMock.mockReset();
    useAutorefillReturn.mockReset();
    updateMock.mockResolvedValue(baseState);
    disableMock.mockResolvedValue(baseState);
  });

  it('показывает loading-skeleton пока запрос грузится', () => {
    useAutorefillReturn.mockReturnValue({ data: undefined, isLoading: true });
    render(withQueryClient(<AutoRefillCard />));
    expect(screen.queryByText(/Авто-пополнение/)).not.toBeInTheDocument();
  });

  it('disabled state: показывает банер «выключено» и кнопку «Включить»', () => {
    useAutorefillReturn.mockReturnValue({ data: baseState, isLoading: false });
    render(withQueryClient(<AutoRefillCard />));
    expect(screen.getByTestId('autorefill-disabled-banner')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Включить авто-пополнение/i }),
    ).toBeInTheDocument();
  });

  it('enabled state: префиллит поля текущими значениями', () => {
    useAutorefillReturn.mockReturnValue({
      data: {
        ...baseState,
        enabled: true,
        threshold_kopecks: toKopecks(50_000) as Kopecks,
        amount_kopecks: toKopecks(500_000) as Kopecks,
        payment_method_id: 'pm-1',
      } satisfies AutorefillState,
      isLoading: false,
    });
    render(withQueryClient(<AutoRefillCard />));
    const threshold = screen.getByTestId('autorefill-threshold') as HTMLInputElement;
    const amount = screen.getByTestId('autorefill-amount') as HTMLInputElement;
    expect(Number(threshold.value)).toBe(500);
    expect(Number(amount.value)).toBe(5_000);
    // Disable-button есть только в enabled state.
    expect(screen.getByTestId('autorefill-disable')).toBeInTheDocument();
  });

  it('3 failures подряд — рендерит error-банер с CTA «Обновить карту»', () => {
    useAutorefillReturn.mockReturnValue({
      data: {
        ...baseState,
        failure_count: 3,
        last_failure_reason: 'Карта истекла',
      } satisfies AutorefillState,
      isLoading: false,
    });
    render(withQueryClient(<AutoRefillCard />));
    expect(screen.getByTestId('autorefill-failures-banner')).toBeInTheDocument();
    expect(screen.getByText(/Карта истекла/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Обновить карту/i })).toHaveAttribute(
      'href',
      '/app/billing',
    );
  });

  it('нет saved_methods — рендерит warning-банер вместо формы', () => {
    useAutorefillReturn.mockReturnValue({
      data: { ...baseState, saved_methods: [] } satisfies AutorefillState,
      isLoading: false,
    });
    render(withQueryClient(<AutoRefillCard />));
    expect(screen.getByTestId('autorefill-no-methods-banner')).toBeInTheDocument();
    expect(screen.queryByTestId('autorefill-save')).not.toBeInTheDocument();
  });

  it('validation: порог > сумма → ошибка inline, save не вызывается', async () => {
    // Стартуем с disabled state'а (default form values: threshold=500, amount=5000).
    // Меняем threshold на 10000 (больше amount=5000) → refine fails при submit.
    useAutorefillReturn.mockReturnValue({ data: baseState, isLoading: false });
    const user = userEvent.setup();
    render(withQueryClient(<AutoRefillCard />));

    fireEvent.change(screen.getByTestId('autorefill-threshold'), {
      target: { value: '10000' },
    });

    await user.click(screen.getByTestId('autorefill-save'));
    expect(
      await screen.findByText(/Порог должен быть ≤ суммы пополнения/i),
    ).toBeInTheDocument();
    expect(updateMock).not.toHaveBeenCalled();
  });

  it('submit: вызывает updateAutorefill с конверсией ₽→копейки', async () => {
    // Default values формы: threshold=500, amount=5000, pm=pm-1. Это уже валидные
    // значения; нам достаточно нажать «Сохранить» — RHF возьмёт defaultValues
    // и пробросит submit. Не вмешиваемся в input'ы — type=number в jsdom +
    // RHF + zod resolver конкретно с valueAsNumber работают неpredictably при
    // fireEvent.change в этой connections-цепочке.
    useAutorefillReturn.mockReturnValue({ data: baseState, isLoading: false });
    const user = userEvent.setup();
    render(withQueryClient(<AutoRefillCard />));

    await user.click(screen.getByTestId('autorefill-save'));
    await waitFor(() => expect(updateMock).toHaveBeenCalled(), { timeout: 3_000 });
    expect(updateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        enabled: true,
        threshold_kopecks: 50_000,
        amount_kopecks: 500_000,
        payment_method_id: 'pm-1',
      }),
    );
  });

  it('Отключить — вызывает disable mutation', async () => {
    useAutorefillReturn.mockReturnValue({
      data: {
        ...baseState,
        enabled: true,
        threshold_kopecks: toKopecks(50_000) as Kopecks,
        amount_kopecks: toKopecks(500_000) as Kopecks,
        payment_method_id: 'pm-1',
      } satisfies AutorefillState,
      isLoading: false,
    });
    const user = userEvent.setup();
    render(withQueryClient(<AutoRefillCard />));
    await user.click(screen.getByTestId('autorefill-disable'));
    await waitFor(() => expect(disableMock).toHaveBeenCalledTimes(1));
  });

  it('select показывает все saved_methods', () => {
    useAutorefillReturn.mockReturnValue({ data: baseState, isLoading: false });
    render(withQueryClient(<AutoRefillCard />));
    const select = screen.getByTestId('autorefill-method') as HTMLSelectElement;
    expect(select.options).toHaveLength(2);
    expect(select.options.item(0)?.text).toContain('Visa');
    expect(select.options.item(0)?.text).toContain('по умолчанию');
    expect(select.options.item(1)?.text).toContain('MasterCard');
  });
});
