/**
 * UX: LoginForm читает ?reason=... из URL и показывает контекстный Banner.
 *
 * Поддерживаемые reason'ы:
 *   - `session_required` — AppLayout редиректнул из-за отсутствия cookie
 *   - `session_expired`  — refresh-flow вернул 401 после успешного refresh
 *   - любое другое значение — Banner НЕ показываем (silent, не ломаем UI на
 *     неизвестных future-reasons до их добавления)
 *
 * Тест имитирует useSearchParams через mock на next/navigation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

// next/navigation: useRouter + useSearchParams — оба нам нужны.
const searchParamsMock = vi.fn(() => new URLSearchParams());
const pushMock = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => searchParamsMock(),
}));

// Toast используется в onError — не должен ломать рендер.
vi.mock('@/components/ui/toast', () => ({
  toast: { error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

// useLogin импортируется через @/lib/auth — мокаем чтобы не дёргать api.
vi.mock('@/lib/auth', () => ({
  useLogin: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

const { LoginForm } = await import('@/components/auth/LoginForm');

function withQueryClient(node: ReactNode): ReactNode {
  // useLogin замокан — но react-hook-form / Banner могут зависеть от QueryClient
  // через провайдер выше. На всякий случай оборачиваем.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('<LoginForm> session reason banner', () => {
  beforeEach(() => {
    searchParamsMock.mockReset();
    pushMock.mockReset();
  });

  it('без reason — banner не рендерится', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams());
    render(withQueryClient(<LoginForm />));
    expect(screen.queryByTestId('login-reason-banner')).toBeNull();
  });

  it('reason=session_required — banner с правильным заголовком', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=session_required'));
    render(withQueryClient(<LoginForm />));
    const banner = screen.getByTestId('login-reason-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('Войди в аккаунт');
    expect(banner.textContent).toContain('нужно войти');
  });

  it('reason=session_expired — banner про истёкшую сессию', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=session_expired'));
    render(withQueryClient(<LoginForm />));
    const banner = screen.getByTestId('login-reason-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('Сессия истекла');
    expect(banner.textContent).toContain('Войди заново');
  });

  it('неизвестный reason — banner НЕ показываем (silent)', () => {
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=mystery'));
    render(withQueryClient(<LoginForm />));
    expect(screen.queryByTestId('login-reason-banner')).toBeNull();
  });

  it('reason=session_required — Banner вариант info, не error', () => {
    // UX: это не ошибка пользователя — это информирование. Красный баннер
    // отпугнул бы при нормальном flow «выйди и войди обратно».
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=session_required'));
    render(withQueryClient(<LoginForm />));
    const banner = screen.getByTestId('login-reason-banner');
    // role=status у info-варианта (см. components/ui/banner.tsx).
    expect(banner.getAttribute('role')).toBe('status');
  });

  it('reason=password_reset_success — success-banner после смены пароля', () => {
    // UX: позитивный переход после редиректа из /reset-password.
    // Зелёный success-вариант отделяет «всё ок» от error/info reason'ов.
    searchParamsMock.mockReturnValue(new URLSearchParams('reason=password_reset_success'));
    render(withQueryClient(<LoginForm />));
    const banner = screen.getByTestId('login-reason-banner');
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('Пароль обновлён');
    expect(banner.textContent).toContain('Войди с новым паролем');
  });
});
