/**
 * <ClosureBanner> — Sprint 7 sticky-top banner для запланированного закрытия.
 *
 * Что покрываем:
 *  - Нет closure → null (банер не рендерится).
 *  - Closure scheduled (Sprint 7 поле) → банер виден с датой.
 *  - Legacy fallback на scheduled_closure_at — банер виден.
 *  - На /app/settings/security → банер скрыт (CloseAccountCard ведёт там сам).
 *  - Cancel-кнопка — вызывает useCancelClosure mutation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { Account } from '@/lib/types';
import { toKopecks } from '@/lib/types';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const useAccountReturn = vi.fn();
const cancelClosureMock = vi.fn();
const usePathnameMock = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => usePathnameMock(),
}));

vi.mock('@/lib/auth', () => ({
  useAccount: () => useAccountReturn(),
  useCancelClosure: () => ({ mutateAsync: cancelClosureMock, isPending: false }),
}));

const { ClosureBanner } = await import('@/components/layout/ClosureBanner');

function buildAccount(overrides?: Partial<Account>): Account {
  return {
    user_id: 'u-1',
    email: 'test@studio.ru',
    account_id: 'a-1',
    name: 'Studio',
    tariff: 'payg',
    balance_kopecks: toKopecks(0),
    prompt_logging_enabled: false,
    notifications: {},
    created_at: '2026-04-01T00:00:00Z',
    email_verified: true,
    ...overrides,
  };
}

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('<ClosureBanner>', () => {
  beforeEach(() => {
    useAccountReturn.mockReset();
    cancelClosureMock.mockReset();
    usePathnameMock.mockReset();
    usePathnameMock.mockReturnValue('/app');
  });

  it('нет closure → банер не рендерится', () => {
    useAccountReturn.mockReturnValue({ data: buildAccount(), isLoading: false });
    const { container } = render(withQueryClient(<ClosureBanner />));
    expect(container.firstChild).toBeNull();
  });

  it('closure_scheduled_at (Sprint 7 поле) → банер виден', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({
        closure_scheduled_at: '2026-05-30T00:00:00Z',
        closure_requested_at: '2026-04-30T00:00:00Z',
      }),
      isLoading: false,
    });
    render(withQueryClient(<ClosureBanner />));
    expect(screen.getByTestId('closure-banner')).toBeInTheDocument();
    expect(screen.getByText(/30 мая 2026/)).toBeInTheDocument();
  });

  it('legacy scheduled_closure_at fallback → банер виден', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({
        closure_scheduled: true,
        scheduled_closure_at: '2026-05-30T00:00:00Z',
      }),
      isLoading: false,
    });
    render(withQueryClient(<ClosureBanner />));
    expect(screen.getByTestId('closure-banner')).toBeInTheDocument();
  });

  it('на /app/settings/security банер скрыт', () => {
    usePathnameMock.mockReturnValue('/app/settings/security');
    useAccountReturn.mockReturnValue({
      data: buildAccount({ closure_scheduled_at: '2026-05-30T00:00:00Z' }),
      isLoading: false,
    });
    const { container } = render(withQueryClient(<ClosureBanner />));
    expect(container.firstChild).toBeNull();
  });

  it('Cancel-кнопка вызывает mutation', async () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ closure_scheduled_at: '2026-05-30T00:00:00Z' }),
      isLoading: false,
    });
    cancelClosureMock.mockResolvedValue({ scheduled: false, scheduled_for: null });
    render(withQueryClient(<ClosureBanner />));
    await userEvent.click(screen.getByTestId('closure-banner-cancel-button'));
    await waitFor(() => expect(cancelClosureMock).toHaveBeenCalled());
  });
});
