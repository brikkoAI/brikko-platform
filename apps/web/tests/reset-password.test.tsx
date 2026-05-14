/**
 * UX-контракт ResetPasswordPage:
 *   1. Без token в URL — показываем «Ссылка недействительна» вместо формы.
 *   2. Минимум 10 символов + match confirm (zod refine).
 *   3. Успех → redirect /login?reason=password_reset_success (НЕ toast).
 *   4. token_expired → понятное сообщение «Ссылка устарела».
 *
 * Почему redirect вместо toast (UX-rationale):
 *   Тост исчезает за 4с. После сброса пароля пользователь часто открывает
 *   password manager — это может занять >10с. Persistent banner на /login
 *   гарантирует, что подтверждение не потеряется.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const resetPasswordMock = vi.fn();
const pushMock = vi.fn();
const toastErrorMock = vi.fn();

let searchParamsImpl: () => URLSearchParams = () =>
  new URLSearchParams('token=valid-token-abc');

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => searchParamsImpl(),
}));

vi.mock('@/components/ui/toast', () => ({
  toast: { error: toastErrorMock, success: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      resetPassword: resetPasswordMock,
    },
  };
});

const { default: ResetPasswordPage } = await import('@/app/reset-password/page');
const { ApiClientError } = await import('@/lib/api');

describe('<ResetPasswordPage>', () => {
  beforeEach(() => {
    resetPasswordMock.mockReset();
    pushMock.mockReset();
    toastErrorMock.mockReset();
    searchParamsImpl = () => new URLSearchParams('token=valid-token-abc');
  });

  it('без token — показывает экран «Ссылка недействительна» вместо формы', () => {
    searchParamsImpl = () => new URLSearchParams('');
    render(<ResetPasswordPage />);
    expect(screen.getByText(/ссылка недействительна/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /запросить новую ссылку/i })).toBeInTheDocument();
    // Форма — не должна быть отрисована.
    expect(screen.queryByLabelText(/новый пароль/i)).toBeNull();
  });

  it('zod: пароль <10 символов — submit заблокирован, API не дёргается', async () => {
    const user = userEvent.setup();
    render(<ResetPasswordPage />);
    await user.type(screen.getByLabelText(/новый пароль/i), 'short');
    await user.type(screen.getByLabelText(/повтори пароль/i), 'short');
    await user.click(screen.getByRole('button', { name: /сохранить новый пароль/i }));
    // Используем role=alert — это ИМЕННО error-сообщение от FieldError, не helper-text.
    // Helper-text "Минимум 10 символов." висит постоянно с таким же текстом.
    const error = await screen.findByRole('alert');
    expect(error).toHaveTextContent(/минимум 10 символов/i);
    expect(resetPasswordMock).not.toHaveBeenCalled();
  });

  it('zod: пароли не совпадают — показываем ошибку под confirm', async () => {
    const user = userEvent.setup();
    render(<ResetPasswordPage />);
    await user.type(screen.getByLabelText(/новый пароль/i), 'longenoughpw1');
    await user.type(screen.getByLabelText(/повтори пароль/i), 'longenoughpw2');
    await user.click(screen.getByRole('button', { name: /сохранить новый пароль/i }));
    expect(await screen.findByText(/пароли не совпадают/i)).toBeInTheDocument();
    expect(resetPasswordMock).not.toHaveBeenCalled();
  });

  it('успех: redirect /login?reason=password_reset_success (без toast)', async () => {
    resetPasswordMock.mockResolvedValueOnce({ ok: true });
    const user = userEvent.setup();
    render(<ResetPasswordPage />);
    await user.type(screen.getByLabelText(/новый пароль/i), 'longenoughpw');
    await user.type(screen.getByLabelText(/повтори пароль/i), 'longenoughpw');
    await user.click(screen.getByRole('button', { name: /сохранить новый пароль/i }));

    await waitFor(() => {
      expect(resetPasswordMock).toHaveBeenCalledWith('valid-token-abc', 'longenoughpw');
    });
    expect(pushMock).toHaveBeenCalledWith('/login?reason=password_reset_success');
  });

  it('token_expired: показывает «Ссылка устарела» через toast', async () => {
    const expired = new ApiClientError(410, {
      type: 'token_expired',
      message: 'expired',
    });
    resetPasswordMock.mockRejectedValueOnce(expired);
    const user = userEvent.setup();
    render(<ResetPasswordPage />);
    await user.type(screen.getByLabelText(/новый пароль/i), 'longenoughpw');
    await user.type(screen.getByLabelText(/повтори пароль/i), 'longenoughpw');
    await user.click(screen.getByRole('button', { name: /сохранить новый пароль/i }));

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(expect.stringMatching(/ссылка устарела/i));
    });
    // На /login не редиректим при ошибке.
    expect(pushMock).not.toHaveBeenCalled();
  });
});
