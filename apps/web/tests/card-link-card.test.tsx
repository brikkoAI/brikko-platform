/**
 * CardLinkCard (CEO 2026-05-15, subscription pivot).
 *
 * Что покрываем — критические кейсы:
 *   1. Loading state — skeleton при первом запросе.
 *   2. Unlinked state — рендерит заголовок «Привяжите карту», CTA disabled
 *      пока чекбокс не отмечен.
 *   3. После consent + клика — link.mutateAsync вызывается с return_url,
 *      содержащим /app/billing/card-linked.
 *   4. Linked state — показывает brand + mask + кнопку «Отвязать карту»
 *      (по умолчанию disabled, потому что unlinkEnabled=false).
 *   5. Backend ошибка card_already_linked → inline banner, не toast.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { AutorefillState } from '@/lib/types';
import { ApiClientError } from '@/lib/api';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const linkMock = vi.fn();
const unlinkMock = vi.fn();
const useAutorefillReturn = vi.fn();

vi.mock('@/lib/auth', () => ({
  useAutorefill: () => useAutorefillReturn(),
  useLinkCard: () => ({ mutateAsync: linkMock, isPending: false }),
  useUnlinkCard: () => ({ mutateAsync: unlinkMock, isPending: false }),
}));

const { CardLinkCard } = await import('@/components/dashboard/CardLinkCard');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

const emptyState: AutorefillState = {
  enabled: false,
  threshold_kopecks: null,
  amount_kopecks: null,
  payment_method_id: null,
  saved_methods: [],
  failure_count: 0,
};

const linkedState: AutorefillState = {
  enabled: false,
  threshold_kopecks: null,
  amount_kopecks: null,
  payment_method_id: 'pm-1',
  saved_methods: [
    {
      id: 'pm-1',
      card_mask: '•••• 1234',
      brand: 'VISA',
      added_at: '2026-05-15T10:00:00Z',
      is_default: true,
    },
  ],
  failure_count: 0,
};

describe('CardLinkCard', () => {
  beforeEach(() => {
    linkMock.mockReset();
    unlinkMock.mockReset();
    useAutorefillReturn.mockReset();
    // jsdom не реализует assign — мокаем чтобы не падать на location.href = …
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { ...window.location, href: '', origin: 'http://localhost:3000' },
    });
  });

  it('показывает skeleton, пока загружаем autorefill snapshot', () => {
    useAutorefillReturn.mockReturnValue({ isLoading: true, data: undefined });
    render(withQueryClient(<CardLinkCard />));
    expect(screen.getByTestId('card-link-loading')).toBeInTheDocument();
  });

  it('Unlinked: CTA disabled без consent, enabled после', async () => {
    useAutorefillReturn.mockReturnValue({ isLoading: false, data: emptyState });
    render(withQueryClient(<CardLinkCard />));

    expect(screen.getByTestId('card-link-unlinked')).toBeInTheDocument();
    expect(screen.getByText(/Привяжите карту — получите 100 ₽/)).toBeInTheDocument();

    const submit = screen.getByTestId('card-link-submit');
    expect(submit).toBeDisabled();

    await userEvent.click(screen.getByTestId('card-link-consent'));
    expect(submit).not.toBeDisabled();
  });

  it('Unlinked: при submit вызывает linkCard с правильным return_url', async () => {
    useAutorefillReturn.mockReturnValue({ isLoading: false, data: emptyState });
    linkMock.mockResolvedValue({ payment_id: 'p1', confirmation_url: 'https://yookassa.ru/c/p1' });

    render(withQueryClient(<CardLinkCard />));

    await userEvent.click(screen.getByTestId('card-link-consent'));
    await userEvent.click(screen.getByTestId('card-link-submit'));

    await waitFor(() => expect(linkMock).toHaveBeenCalledTimes(1));
    const payload = linkMock.mock.calls[0]?.[0] as { return_url: string };
    expect(payload.return_url).toContain('/app/billing/card-linked?status=success');
  });

  it('Unlinked: показывает inline banner при card_already_linked', async () => {
    useAutorefillReturn.mockReturnValue({ isLoading: false, data: emptyState });
    linkMock.mockRejectedValue(
      new ApiClientError(409, {
        type: 'card_already_linked',
        message: 'Карта уже привязана',
      }),
    );

    render(withQueryClient(<CardLinkCard />));
    await userEvent.click(screen.getByTestId('card-link-consent'));
    await userEvent.click(screen.getByTestId('card-link-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('card-link-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('card-link-error')).toHaveTextContent(/уже привязана/i);
  });

  it('Linked: показывает brand+mask и disabled-кнопку «Отвязать карту»', () => {
    useAutorefillReturn.mockReturnValue({ isLoading: false, data: linkedState });
    render(withQueryClient(<CardLinkCard />));

    expect(screen.getByTestId('card-link-linked')).toBeInTheDocument();
    expect(screen.getByText(/VISA/)).toBeInTheDocument();
    expect(screen.getByText(/•••• 1234/)).toBeInTheDocument();

    const unlinkBtn = screen.getByTestId('card-link-unlink');
    expect(unlinkBtn).toBeDisabled();
  });

  it('Linked: с unlinkEnabled — кнопка активна и вызывает unlink', async () => {
    useAutorefillReturn.mockReturnValue({ isLoading: false, data: linkedState });
    unlinkMock.mockResolvedValue({ ok: true });

    render(withQueryClient(<CardLinkCard unlinkEnabled />));

    const unlinkBtn = screen.getByTestId('card-link-unlink');
    expect(unlinkBtn).not.toBeDisabled();

    await userEvent.click(unlinkBtn);
    await waitFor(() => expect(unlinkMock).toHaveBeenCalledTimes(1));
  });
});
