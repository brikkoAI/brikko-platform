/**
 * Security tab — Sprint 6.
 *
 * Что покрываем:
 *  - Карточка 2FA: disabled-state показывает «Включить», enabled — «Отключить» + дата.
 *  - Sessions card: рендерит список с current-badge, click revoke вызывает mutation.
 *  - ChangePasswordCard: validation + 401 на текущий пароль.
 *  - CloseAccountCard: confirm-фраза работает; кнопка скрыта когда closure scheduled.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { Account, AuthSession } from '@/lib/types';
import { toKopecks } from '@/lib/types';
import { ApiClientError } from '@/lib/api';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const useAccountReturn = vi.fn();
const useSessionsReturn = vi.fn();
const revokeSessionMock = vi.fn();
const revokeAllSessionsMock = vi.fn();
const setupMock = vi.fn();
const verifyMock = vi.fn();
const disableMock = vi.fn();
const changePasswordMock = vi.fn();
const closeAccountMock = vi.fn();
const cancelClosureMock = vi.fn();

vi.mock('@/lib/auth', () => ({
  useAccount: () => useAccountReturn(),
  useSessions: () => useSessionsReturn(),
  useRevokeSession: () => ({ mutateAsync: revokeSessionMock, isPending: false }),
  useRevokeAllSessions: () => ({ mutateAsync: revokeAllSessionsMock, isPending: false }),
  useTwoFactorSetup: () => ({ mutateAsync: setupMock, isPending: false }),
  useTwoFactorVerify: () => ({ mutateAsync: verifyMock, isPending: false }),
  useTwoFactorDisable: () => ({ mutateAsync: disableMock, isPending: false }),
  useChangePasswordV2: () => ({ mutateAsync: changePasswordMock, isPending: false }),
  useCloseAccount: () => ({ mutateAsync: closeAccountMock, isPending: false }),
  useCancelClosure: () => ({ mutateAsync: cancelClosureMock, isPending: false }),
}));

const { TwoFactorCard } = await import('@/components/dashboard/settings/TwoFactorCard');
const { ActiveSessionsCard } = await import('@/components/dashboard/settings/ActiveSessionsCard');
const { ChangePasswordCard } = await import('@/components/dashboard/settings/ChangePasswordCard');
const { CloseAccountCard } = await import('@/components/dashboard/settings/CloseAccountCard');

function buildAccount(overrides?: Partial<Account>): Account {
  return {
    user_id: 'u-1',
    email: 'test@studio.ru',
    account_id: 'a-1',
    name: 'Studio',
    tariff: 'payg',
    balance_kopecks: toKopecks(50_000),
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

describe('<TwoFactorCard>', () => {
  beforeEach(() => {
    useAccountReturn.mockReset();
    setupMock.mockReset();
  });

  it('2FA выключена — primary CTA «Включить 2FA»', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ two_factor_enabled: false }),
      isLoading: false,
    });
    render(withQueryClient(<TwoFactorCard />));
    expect(screen.getByTestId('2fa-enable-button')).toBeInTheDocument();
    expect(screen.queryByTestId('2fa-disable-button')).toBeNull();
  });

  it('2FA включена — отображается «Включена» badge + дата + Disable CTA', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({
        two_factor_enabled: true,
        two_factor_enabled_at: '2026-04-15T00:00:00Z',
      }),
      isLoading: false,
    });
    render(withQueryClient(<TwoFactorCard />));
    expect(screen.getByText('Включена')).toBeInTheDocument();
    expect(screen.getByText(/Включена 15 апр/)).toBeInTheDocument();
    expect(screen.getByTestId('2fa-disable-button')).toBeInTheDocument();
  });
});

describe('<ActiveSessionsCard>', () => {
  beforeEach(() => {
    useSessionsReturn.mockReset();
    revokeSessionMock.mockReset();
    revokeAllSessionsMock.mockReset();
  });

  it('current session — отображается с badge «Текущая», без revoke-кнопки', () => {
    const sessions: AuthSession[] = [
      {
        id: 's1',
        device: 'Chrome on Windows',
        ip: '1.2.3.4',
        last_active_at: new Date().toISOString(),
        is_current: true,
      },
    ];
    useSessionsReturn.mockReturnValue({ data: sessions, isLoading: false });
    render(withQueryClient(<ActiveSessionsCard />));
    expect(screen.getByText('Текущая')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Завершить/i })).toBeNull();
  });

  it('revoke сессии — вызывает mutation', async () => {
    const sessions: AuthSession[] = [
      {
        id: 'current',
        device: 'Chrome on Windows',
        ip: '1.2.3.4',
        last_active_at: new Date().toISOString(),
        is_current: true,
      },
      {
        id: 'mobile',
        device: 'Safari on iOS',
        ip: '5.6.7.8',
        last_active_at: new Date().toISOString(),
        is_current: false,
      },
    ];
    useSessionsReturn.mockReturnValue({ data: sessions, isLoading: false });
    revokeSessionMock.mockResolvedValue({ ok: true });
    render(withQueryClient(<ActiveSessionsCard />));
    await userEvent.click(
      screen.getByRole('button', { name: /Завершить сессию Safari on iOS/i }),
    );
    await waitFor(() => expect(revokeSessionMock).toHaveBeenCalledWith('mobile'));
  });

  it('revoke all — вызывает массовый revoke', async () => {
    const sessions: AuthSession[] = [
      { id: 'current', device: 'Chrome on Windows', ip: '1.2.3.4', last_active_at: new Date().toISOString(), is_current: true },
      { id: 'mobile', device: 'Safari on iOS', ip: '5.6.7.8', last_active_at: new Date().toISOString(), is_current: false },
    ];
    useSessionsReturn.mockReturnValue({ data: sessions, isLoading: false });
    revokeAllSessionsMock.mockResolvedValue({ ok: true });
    render(withQueryClient(<ActiveSessionsCard />));
    await userEvent.click(screen.getByTestId('revoke-all-sessions-button'));
    await waitFor(() => expect(revokeAllSessionsMock).toHaveBeenCalled());
  });
});

describe('<ChangePasswordCard>', () => {
  beforeEach(() => {
    changePasswordMock.mockReset();
  });

  it('валидация: новый пароль <8 символов — error', async () => {
    render(withQueryClient(<ChangePasswordCard />));
    await userEvent.type(screen.getByLabelText('Текущий пароль'), 'old-password-1');
    await userEvent.type(screen.getByLabelText('Новый пароль'), 'short');
    await userEvent.type(screen.getByLabelText('Повтори новый'), 'short');
    await userEvent.click(screen.getByRole('button', { name: /Сменить пароль/i }));
    expect(await screen.findByText('Минимум 8 символов')).toBeInTheDocument();
    expect(changePasswordMock).not.toHaveBeenCalled();
  });

  it('happy path — отправляет current+new', async () => {
    changePasswordMock.mockResolvedValue({ ok: true });
    render(withQueryClient(<ChangePasswordCard />));
    await userEvent.type(screen.getByLabelText('Текущий пароль'), 'old-password-1');
    await userEvent.type(screen.getByLabelText('Новый пароль'), 'new-password-2');
    await userEvent.type(screen.getByLabelText('Повтори новый'), 'new-password-2');
    await userEvent.click(screen.getByRole('button', { name: /Сменить пароль/i }));
    await waitFor(() =>
      expect(changePasswordMock).toHaveBeenCalledWith({
        current_password: 'old-password-1',
        new_password: 'new-password-2',
      }),
    );
  });

  it('401 — показывает «Текущий пароль не совпадает» под полем', async () => {
    changePasswordMock.mockRejectedValue(
      new ApiClientError(401, { type: 'invalid_credentials', message: 'Неверный пароль' }),
    );
    render(withQueryClient(<ChangePasswordCard />));
    await userEvent.type(screen.getByLabelText('Текущий пароль'), 'wrong-password-1');
    await userEvent.type(screen.getByLabelText('Новый пароль'), 'new-password-2');
    await userEvent.type(screen.getByLabelText('Повтори новый'), 'new-password-2');
    await userEvent.click(screen.getByRole('button', { name: /Сменить пароль/i }));
    expect(await screen.findByText('Текущий пароль не совпадает')).toBeInTheDocument();
  });
});

describe('<CloseAccountCard>', () => {
  beforeEach(() => {
    closeAccountMock.mockReset();
  });

  it('closure не запланировано — кнопка «Закрыть аккаунт» видима', () => {
    render(withQueryClient(<CloseAccountCard closureScheduled={false} />));
    expect(screen.getByTestId('close-account-button')).toBeInTheDocument();
  });

  it('closure уже scheduled — кнопка скрыта, показывается info', () => {
    render(withQueryClient(<CloseAccountCard closureScheduled={true} />));
    expect(screen.queryByTestId('close-account-button')).toBeNull();
    expect(screen.getByText(/Закрытие уже запланировано/)).toBeInTheDocument();
  });

  it('confirm: без правильной фразы — error, mutation не вызывается', async () => {
    render(withQueryClient(<CloseAccountCard closureScheduled={false} />));
    await userEvent.click(screen.getByTestId('close-account-button'));
    await userEvent.type(screen.getByTestId('close-phrase-input'), 'wrong');
    await userEvent.click(screen.getByTestId('close-account-confirm-cta'));
    expect(
      await screen.findByText(/Введи «BRIKKO CLOSE» точно как написано/),
    ).toBeInTheDocument();
    expect(closeAccountMock).not.toHaveBeenCalled();
  });

  it('confirm: правильная фраза + reason — mutation с reason', async () => {
    closeAccountMock.mockResolvedValue({ scheduled: true, scheduled_for: '2026-05-30T00:00:00Z' });
    render(withQueryClient(<CloseAccountCard closureScheduled={false} />));
    await userEvent.click(screen.getByTestId('close-account-button'));
    await userEvent.type(screen.getByTestId('close-reason-input'), 'нет PII-маскинга на free');
    await userEvent.type(screen.getByTestId('close-phrase-input'), 'BRIKKO CLOSE');
    await userEvent.click(screen.getByTestId('close-account-confirm-cta'));
    await waitFor(() =>
      expect(closeAccountMock).toHaveBeenCalledWith('нет PII-маскинга на free'),
    );
  });
});
