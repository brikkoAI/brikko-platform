/**
 * UX-контракт ForgotPasswordPage:
 *   1. Email zod-валидируется до сетевого вызова.
 *   2. После успеха — экран «Ссылка отправлена» с подтверждением email.
 *   3. Сообщение нейтральное: "Если аккаунт ... существует" — не подтверждаем
 *      существование email (защита от user-enumeration на /forgot-password).
 *   4. Сетевая ошибка не валит форму — toast и пользователь может попробовать снова.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const forgotPasswordMock = vi.fn();
const toastErrorMock = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
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
      forgotPassword: forgotPasswordMock,
    },
  };
});

const { default: ForgotPasswordPage } = await import('@/app/forgot-password/page');

describe('<ForgotPasswordPage>', () => {
  beforeEach(() => {
    forgotPasswordMock.mockReset();
    toastErrorMock.mockReset();
  });

  it('zod-валидация: блокирует submit при пустом email и не зовёт API', async () => {
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);
    await user.click(screen.getByRole('button', { name: /отправить ссылку/i }));
    expect(await screen.findByText(/введи email/i)).toBeInTheDocument();
    expect(forgotPasswordMock).not.toHaveBeenCalled();
  });

  it('успех: показывает success-screen с email и нейтральной формулировкой', async () => {
    forgotPasswordMock.mockResolvedValueOnce({ ok: true });
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);
    await user.type(screen.getByLabelText(/email/i), 'TEST@Example.com');
    await user.click(screen.getByRole('button', { name: /отправить ссылку/i }));

    await waitFor(() => {
      expect(forgotPasswordMock).toHaveBeenCalledWith('test@example.com');
    });
    // success-screen: нейтральное "Если аккаунт ... существует"
    expect(await screen.findByText(/если аккаунт с email/i)).toBeInTheDocument();
    expect(screen.getByText(/test@example\.com/i)).toBeInTheDocument();
    expect(screen.getByText(/ссылка действует 1 час/i)).toBeInTheDocument();
  });

  it('ошибка сети: показывает toast и оставляет форму открытой для retry', async () => {
    forgotPasswordMock.mockRejectedValueOnce(new Error('boom'));
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);
    await user.type(screen.getByLabelText(/email/i), 'a@b.io');
    await user.click(screen.getByRole('button', { name: /отправить ссылку/i }));

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalled();
    });
    // Форма всё ещё на месте — success-screen НЕ показан.
    expect(screen.queryByText(/ссылка отправлена/i)).toBeNull();
  });
});
