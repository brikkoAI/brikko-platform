/**
 * /auth/verify-email — Sprint 6 expired-state.
 *
 * Что покрываем:
 *  - ?error=expired → специальный заголовок «Ссылка просрочена».
 *  - ?email=... pre-fill в input.
 *  - Resend mutation с lowercased email.
 *  - Cooldown UI (5 мин) — после resend кнопка disabled с countdown.
 *  - Невалидный email — error на форме, mutation не вызывается.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

const searchParamsMock = vi.fn(() => new URLSearchParams());
vi.mock('next/navigation', () => ({
  useSearchParams: () => searchParamsMock(),
}));

const toastSuccess = vi.fn();
vi.mock('@/components/ui/toast', () => ({
  toast: { success: toastSuccess, error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const resendMock = vi.fn();
vi.mock('@/lib/auth', () => ({
  useResendEmailVerification: () => ({ mutateAsync: resendMock, isPending: false }),
}));

const { default: VerifyEmailExpiredPage } = await import('@/app/auth/verify-email/page');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('/auth/verify-email expired-state', () => {
  beforeEach(() => {
    searchParamsMock.mockReset();
    resendMock.mockReset();
    toastSuccess.mockReset();
  });

  it('?error=expired — заголовок «Ссылка просрочена»', () => {
    // Sprint 13 редизайн (apps/web/src/app/auth/verify-email/page.tsx):
    // длинный H1 «Ссылка для подтверждения email просрочена» сокращён до
    // «Ссылка просрочена» — короче и помещается в auth-card на mobile.
    // Семантика осталась той же; тест проверяет именно expired-state, а
    // не точную копирайт-формулировку.
    searchParamsMock.mockReturnValue(new URLSearchParams('error=expired'));
    render(withQueryClient(<VerifyEmailExpiredPage />));
    expect(screen.getByText('Ссылка просрочена')).toBeInTheDocument();
  });

  it('?email=... pre-fill в input', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('error=expired&email=test@studio.ru'));
    render(withQueryClient(<VerifyEmailExpiredPage />));
    expect((screen.getByTestId('verify-email-input') as HTMLInputElement).value).toBe(
      'test@studio.ru',
    );
  });

  it('happy path: click «Прислать новое» — resend вызывается с lowercased email', async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('error=expired&email=Test@Studio.RU'));
    resendMock.mockResolvedValue({ ok: true });
    render(withQueryClient(<VerifyEmailExpiredPage />));
    await userEvent.click(screen.getByTestId('resend-verification-cta'));
    await waitFor(() => expect(resendMock).toHaveBeenCalledWith('test@studio.ru'));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('после успешного resend — кнопка disable с countdown', async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('error=expired&email=test@studio.ru'));
    resendMock.mockResolvedValue({ ok: true });
    render(withQueryClient(<VerifyEmailExpiredPage />));
    await userEvent.click(screen.getByTestId('resend-verification-cta'));
    await waitFor(() => {
      const cta = screen.getByTestId('resend-verification-cta');
      expect(cta).toBeDisabled();
      // В кнопке появляется «Повторно через M:SS».
      expect(cta.textContent).toMatch(/Повторно через/);
    });
  });

  it('невалидный email — error, mutation не вызывается', async () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('error=expired'));
    render(withQueryClient(<VerifyEmailExpiredPage />));
    await userEvent.type(screen.getByTestId('verify-email-input'), 'not-an-email');
    await userEvent.click(screen.getByTestId('resend-verification-cta'));
    expect(await screen.findByText(/Проверь формат email/)).toBeInTheDocument();
    expect(resendMock).not.toHaveBeenCalled();
  });
});
