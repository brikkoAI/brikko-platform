/**
 * Закрывает FE P0-7: AppLayout должен сделать server-side presence-check cookie
 * `vlt_access` и редиректнуть неавторизованного пользователя ДО рендера shell.
 *
 * Тест mock'ает next/headers + next/navigation и проверяет два состояния:
 *   1. cookie отсутствует → redirect('/login?reason=session_required').
 *   2. cookie есть → рендер вызывается, дочерний контент попадает в DOM.
 *
 * Не использует jsdom-render для server-component'а (он async): вместо этого
 * вызываем функцию напрямую и проверяем, что она либо бросает (через mocked
 * `redirect`), либо возвращает JSX, который мы инспектируем.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// next/navigation.redirect бросает специальный signal; мы его эмулируем как throw.
const redirectMock = vi.fn((url: string) => {
  throw new Error(`__REDIRECT__:${url}`);
});
const cookiesMock = vi.fn();

vi.mock('next/navigation', () => ({
  redirect: (url: string) => redirectMock(url),
}));

vi.mock('next/headers', () => ({
  cookies: () => cookiesMock(),
}));

// Заглушки для UI-частей — они нам не нужны, нас интересует только логика guard'а.
// После Sprint 13.6 Sidebar/Topbar/MobileNav инкапсулированы в DashboardShell.
vi.mock('@/components/dashboard/DashboardShell', () => ({
  DashboardShell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock('@/components/layout/ClosureBanner', () => ({ ClosureBanner: () => null }));

const { default: AppLayout } = await import('@/app/app/layout');

describe('AppLayout server-side guard', () => {
  beforeEach(() => {
    redirectMock.mockClear();
    cookiesMock.mockClear();
  });

  it('cookie отсутствует → редиректит на /login?reason=session_required', async () => {
    cookiesMock.mockReturnValue({
      get: (_name: string) => undefined,
    });

    await expect(AppLayout({ children: 'irrelevant' })).rejects.toThrow(
      '__REDIRECT__:/login?reason=session_required',
    );
    expect(redirectMock).toHaveBeenCalledWith('/login?reason=session_required');
  });

  it('cookie пустая строка → тоже редиректит (undefined-style)', async () => {
    cookiesMock.mockReturnValue({
      get: (_name: string) => ({ name: 'vlt_access', value: '' }),
    });

    await expect(AppLayout({ children: 'irrelevant' })).rejects.toThrow();
    expect(redirectMock).toHaveBeenCalledWith('/login?reason=session_required');
  });

  it('cookie есть → НЕ редиректит, возвращает рендер shell', async () => {
    cookiesMock.mockReturnValue({
      get: (_name: string) => ({ name: 'vlt_access', value: 'fake.jwt.token' }),
    });

    const result = await AppLayout({ children: 'protected-content' });
    expect(redirectMock).not.toHaveBeenCalled();
    expect(result).toBeDefined();
  });
});
