/**
 * SettingsPage (Sprint 4 / Поток O): PII-маскинг tier-gate + Telegram link bootstrap.
 *
 * Что покрываем:
 *   - На non-Privacy тарифе toggle PII disabled + Banner с upgrade CTA.
 *   - На Privacy тарифе toggle активен и triggerит updateSettings({pii_masking_enabled}).
 *   - Кнопка «Подключить Telegram» вызывает linkTelegram и показывает deep_link.
 *   - При уже привязанном TG — рендерим Disconnect.
 *
 * Mocking-стратегия:
 *   - useAccount/useUpdateSettings/useLogout/useLinkTelegram/useUnlinkTelegram все мокаем
 *     через vi.mock('@/lib/auth'). Это стандартный pattern в существующих тестах
 *     (см. login-form.test.tsx).
 *   - Toast мокается no-op'ом — мы не проверяем UX-сторону уведомлений.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { Account } from '@/lib/types';
import { toKopecks } from '@/lib/types';

// Toast — no-op; не должен ломать рендер.
vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

// auth-хуки — мокаем все вызываемые SettingsPage.
const updateSettingsMock = vi.fn();
const logoutMock = vi.fn();
const linkTelegramMock = vi.fn();
const unlinkTelegramMock = vi.fn();
const useAccountReturn = vi.fn();

vi.mock('@/lib/auth', () => ({
  useAccount: () => useAccountReturn(),
  useUpdateSettings: () => ({
    mutateAsync: updateSettingsMock,
    isPending: false,
  }),
  useLogout: () => ({
    mutateAsync: logoutMock,
    isPending: false,
  }),
  useLinkTelegram: () => ({
    mutateAsync: linkTelegramMock,
    isPending: false,
  }),
  useUnlinkTelegram: () => ({
    mutateAsync: unlinkTelegramMock,
    isPending: false,
  }),
  // Sprint 6: SettingsPage теперь рендерит DataExportSection, который использует эти hooks.
  useDataExportLatest: () => ({ data: null, isLoading: false }),
  useRequestDataExport: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // Sprint 7: production data-export — список + polling.
  useDataExportList: () => ({ data: [], isLoading: false }),
  useDataExportStatus: () => ({ data: undefined, isFetching: false }),
}));

const { default: SettingsPage } = await import('@/app/app/settings/page');

function buildAccount(overrides?: Partial<Account>): Account {
  return {
    user_id: 'u-1',
    email: 'test@studio.ru',
    account_id: 'a-1',
    name: 'Studio',
    tariff: 'pro',
    balance_kopecks: toKopecks(100_000),
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

describe('<SettingsPage> PII-маскинг tier-gate', () => {
  beforeEach(() => {
    updateSettingsMock.mockReset();
    linkTelegramMock.mockReset();
    unlinkTelegramMock.mockReset();
    useAccountReturn.mockReset();
  });

  it('non-Privacy тариф (Pro) — toggle disabled + upgrade banner', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ tariff: 'pro' }),
      isLoading: false,
    });
    render(withQueryClient(<SettingsPage />));

    const toggle = screen.getByTestId('pii-masking-toggle') as HTMLInputElement;
    expect(toggle.disabled).toBe(true);
    expect(toggle.checked).toBe(false);

    // Banner с upgrade CTA.
    expect(screen.getByText(/Доступно на тарифах Privacy/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Перейти на Privacy-тариф/i })).toHaveAttribute(
      'href',
      '/app/billing',
    );
  });

  it('Pro Privacy — toggle активен, выключен по умолчанию', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ tariff: 'pro_privacy', pii_masking_enabled: false }),
      isLoading: false,
    });
    render(withQueryClient(<SettingsPage />));

    const toggle = screen.getByTestId('pii-masking-toggle') as HTMLInputElement;
    expect(toggle.disabled).toBe(false);
    expect(toggle.checked).toBe(false);
    expect(screen.queryByText(/Доступно на тарифах Privacy/i)).not.toBeInTheDocument();
  });

  it('Включение PII вызывает updateSettings({pii_masking_enabled:true})', async () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ tariff: 'business_privacy' }),
      isLoading: false,
    });
    updateSettingsMock.mockResolvedValue(buildAccount({ pii_masking_enabled: true }));

    render(withQueryClient(<SettingsPage />));
    const toggle = screen.getByTestId('pii-masking-toggle') as HTMLInputElement;
    await userEvent.click(toggle);

    await waitFor(() =>
      expect(updateSettingsMock).toHaveBeenCalledWith({ pii_masking_enabled: true }),
    );
  });

  it('Business+ — PII доступно (tier-gate включает business_plus)', () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ tariff: 'business_plus', pii_masking_enabled: true }),
      isLoading: false,
    });
    render(withQueryClient(<SettingsPage />));

    const toggle = screen.getByTestId('pii-masking-toggle') as HTMLInputElement;
    expect(toggle.disabled).toBe(false);
    expect(toggle.checked).toBe(true);
  });
});

describe('<SettingsPage> Telegram-секция', () => {
  beforeEach(() => {
    updateSettingsMock.mockReset();
    linkTelegramMock.mockReset();
    unlinkTelegramMock.mockReset();
    useAccountReturn.mockReset();
  });

  it('не подключён — кнопка «Подключить Telegram» вызывает linkTelegram', async () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({ telegram_link: { linked: false, chat_id: null } }),
      isLoading: false,
    });
    linkTelegramMock.mockResolvedValue({
      token: 'abc123',
      ttl_seconds: 300,
      bot_username: 'VoltariBot',
      deep_link: 'https://t.me/VoltariBot?start=abc123',
    });

    render(withQueryClient(<SettingsPage />));
    await userEvent.click(screen.getByTestId('telegram-link-button'));

    await waitFor(() => expect(linkTelegramMock).toHaveBeenCalled());
    // Deep-link должен отрендериться в коде <code>.
    expect(screen.getByTestId('telegram-deep-link').textContent).toContain(
      'https://t.me/VoltariBot?start=abc123',
    );
  });

  it('уже подключён — рендерится chat_id и кнопка Отключить', async () => {
    useAccountReturn.mockReturnValue({
      data: buildAccount({
        telegram_link: { linked: true, chat_id: '123456789' },
      }),
      isLoading: false,
    });
    unlinkTelegramMock.mockResolvedValue({ ok: true });

    render(withQueryClient(<SettingsPage />));
    expect(screen.getByText('123456789')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Отключить/i }));
    await waitFor(() => expect(unlinkTelegramMock).toHaveBeenCalled());
  });
});
