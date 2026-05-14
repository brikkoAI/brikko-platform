/**
 * <UpgradeConfirmModal> — Sprint 6.
 *
 * Что покрываем:
 *  - Hand-shake UX: показ «Текущий баланс» и «Стоимость подписки».
 *  - Insufficient balance — кнопка «Списать» disabled + warning banner.
 *  - 402 от backend — банер «Недостаточно средств» + кнопка «Пополнить».
 *  - Privacy-апгрейд — info-плашка «после апгрейда вернёмся в Privacy».
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { Kopecks } from '@/lib/types';
import { toKopecks } from '@/lib/types';
import { ApiClientError } from '@/lib/api';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const changeTariffMock = vi.fn();

vi.mock('@/lib/auth', () => ({
  useChangeTariff: () => ({
    mutateAsync: changeTariffMock,
    isPending: false,
    reset: vi.fn(),
  }),
}));

const { UpgradeConfirmModal } = await import('@/components/dashboard/UpgradeConfirmModal');
const { TARIFF_CATALOG } = await import('@/components/dashboard/TariffCard');

const PRO_PRIVACY = TARIFF_CATALOG.find((t) => t.slug === 'pro_privacy')!;

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('<UpgradeConfirmModal>', () => {
  beforeEach(() => {
    changeTariffMock.mockReset();
  });

  it('баланса хватает — confirm CTA active, отправляет slug', async () => {
    changeTariffMock.mockResolvedValue({
      tariff: 'pro_privacy',
      tariff_active_until: '2026-05-30T00:00:00Z',
      requires_pii_setup: true,
    });
    const onSuccess = vi.fn();
    render(
      withQueryClient(
        <UpgradeConfirmModal
          open
          onOpenChange={() => {}}
          tariff={PRO_PRIVACY}
          balanceKopecks={toKopecks(500_000)}
          priceKopecks={279_000}
          onSuccess={onSuccess}
        />,
      ),
    );
    const cta = screen.getByTestId('upgrade-confirm-cta');
    expect(cta).not.toBeDisabled();
    await userEvent.click(cta);
    expect(changeTariffMock).toHaveBeenCalledWith('pro_privacy');
    // onSuccess вызван — родитель закроет модал и сделает redirect.
    expect(onSuccess).toHaveBeenCalledWith(
      expect.objectContaining({ tariff: 'pro_privacy', requires_pii_setup: true }),
    );
  });

  it('недостаточно средств — banner + Пополнить ссылка', () => {
    render(
      withQueryClient(
        <UpgradeConfirmModal
          open
          onOpenChange={() => {}}
          tariff={PRO_PRIVACY}
          balanceKopecks={toKopecks(10_000) as Kopecks /* 100 ₽ */}
          priceKopecks={279_000} // 2 790 ₽
          onSuccess={() => {}}
        />,
      ),
    );
    expect(screen.getByText('Недостаточно средств')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Пополнить/i })).toHaveAttribute(
      'href',
      '/app/billing',
    );
    // Confirm-кнопка disabled.
    expect(screen.getByTestId('upgrade-confirm-cta')).toBeDisabled();
  });

  it('402 от backend — показывает insufficient banner с message', async () => {
    changeTariffMock.mockRejectedValue(
      new ApiClientError(402, {
        type: 'insufficient_balance',
        message: 'Недостаточно средств: нужно 2 790 ₽, на балансе 100 ₽',
      }),
    );
    render(
      withQueryClient(
        <UpgradeConfirmModal
          open
          onOpenChange={() => {}}
          tariff={PRO_PRIVACY}
          balanceKopecks={toKopecks(500_000)} // даём фальшиво ≥ price чтобы пройти client-side
          priceKopecks={279_000}
          onSuccess={() => {}}
        />,
      ),
    );
    await userEvent.click(screen.getByTestId('upgrade-confirm-cta'));
    // Сообщение из backend появляется в banner.
    expect(await screen.findByText(/Недостаточно средств: нужно/)).toBeInTheDocument();
  });

  it('Privacy-тариф — info-плашка про возврат в Privacy секцию', () => {
    render(
      withQueryClient(
        <UpgradeConfirmModal
          open
          onOpenChange={() => {}}
          tariff={PRO_PRIVACY}
          balanceKopecks={toKopecks(500_000)}
          priceKopecks={279_000}
          onSuccess={() => {}}
        />,
      ),
    );
    expect(screen.getByText(/PII-маскинг/)).toBeInTheDocument();
  });
});
