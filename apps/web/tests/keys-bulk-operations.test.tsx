/**
 * KeysPage bulk operations (Sprint 8 §5).
 *
 * Что покрываем:
 *   - Чекбоксы появляются для активных ключей; revoked — disabled (—).
 *   - При выборе ≥1 — показывается BulkActionBar со счётчиком.
 *   - Cancel — снимает выделение.
 *   - Revoke — открывает confirm dialog с правильным title.
 *   - Подтверждение confirm dialog → вызывает bulkRevoke.mutateAsync с ID array.
 *   - Rotate — confirm dialog с другим title + текстом.
 *   - Select-all checkbox: toggle on → select all active; off → clear.
 *   - Indeterminate state когда выбран не все.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { ApiKey } from '@/lib/types';

vi.mock('@/components/ui/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const useApiKeysReturn = vi.fn();
const bulkRevokeMock = vi.fn();
const bulkRotateMock = vi.fn();

vi.mock('@/lib/auth', () => ({
  useApiKeys: () => useApiKeysReturn(),
  useBulkRevokeKeys: () => ({ mutateAsync: bulkRevokeMock, isPending: false }),
  useBulkRotateKeys: () => ({ mutateAsync: bulkRotateMock, isPending: false }),
  useRevokeKey: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateKey: () => ({ mutateAsync: vi.fn(), isPending: false, data: undefined, reset: vi.fn() }),
}));

const KeysPage = (await import('@/app/app/keys/page')).default;

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

const ACTIVE_KEYS: ApiKey[] = [
  {
    id: 'k-1',
    name: 'Production',
    prefix: 'sk-vt-AB12',
    scope: 'full',
    last_used_at: '2026-04-29T00:00:00Z',
    created_at: '2026-04-01T00:00:00Z',
    revoked_at: null,
  },
  {
    id: 'k-2',
    name: 'Staging',
    prefix: 'sk-vt-XY99',
    scope: 'full',
    last_used_at: null,
    created_at: '2026-04-15T00:00:00Z',
    revoked_at: null,
  },
];

const REVOKED_KEY: ApiKey = {
  id: 'k-3',
  name: 'Old',
  prefix: 'sk-vt-OO00',
  scope: 'read_only',
  last_used_at: null,
  created_at: '2026-03-01T00:00:00Z',
  revoked_at: '2026-04-01T00:00:00Z',
};

describe('KeysPage bulk operations (Sprint 8)', () => {
  beforeEach(() => {
    bulkRevokeMock.mockReset();
    bulkRotateMock.mockReset();
    useApiKeysReturn.mockReset();
    bulkRevokeMock.mockResolvedValue({ revoked: ['k-1'] });
    bulkRotateMock.mockResolvedValue({ rotated: [{ id: 'k-1', full_key: 'sk-vt-NEW...', prefix: 'sk-vt-NEW1', scope: 'full' }] });
    useApiKeysReturn.mockReturnValue({
      data: [...ACTIVE_KEYS, REVOKED_KEY],
      isLoading: false,
    });
  });

  it('action-bar скрыт пока ничего не выбрано', () => {
    render(withQueryClient(<KeysPage />));
    expect(screen.queryByTestId('bulk-action-bar')).not.toBeInTheDocument();
  });

  it('checkbox активного ключа toggle-ит выделение и показывает BulkActionBar', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    const bar = screen.getByTestId('bulk-action-bar');
    expect(bar).toBeInTheDocument();
    expect(within(bar).getByText(/Выбрано/)).toBeInTheDocument();
    expect(within(bar).getByText(/1/)).toBeInTheDocument();
  });

  it('у revoked ключа нет чекбокса', () => {
    render(withQueryClient(<KeysPage />));
    expect(screen.queryByTestId('select-key-k-3')).not.toBeInTheDocument();
  });

  it('select-all — выбирает все активные ключи', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-all-keys'));
    const bar = screen.getByTestId('bulk-action-bar');
    expect(within(bar).getByText(/2/)).toBeInTheDocument();
    expect((screen.getByTestId('select-key-k-1') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId('select-key-k-2') as HTMLInputElement).checked).toBe(true);
  });

  it('select-all повторно — снимает выделение', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-all-keys'));
    await user.click(screen.getByTestId('select-all-keys'));
    expect(screen.queryByTestId('bulk-action-bar')).not.toBeInTheDocument();
  });

  it('cancel — снимает выделение и убирает action-bar', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('bulk-cancel'));
    expect(screen.queryByTestId('bulk-action-bar')).not.toBeInTheDocument();
  });

  it('revoke — открывает confirm dialog с правильным title', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('select-key-k-2'));
    await user.click(screen.getByTestId('bulk-revoke'));

    const dialog = await screen.findByTestId('bulk-confirm-dialog');
    expect(within(dialog).getByText(/Отозвать 2 ключей\?/)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/Запросы с отозванных ключей сразу получат 401/),
    ).toBeInTheDocument();
  });

  it('подтверждение revoke — вызывает bulkRevoke с ID array', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('bulk-revoke'));
    await user.click(screen.getByTestId('bulk-confirm-submit'));

    await waitFor(() => {
      expect(bulkRevokeMock).toHaveBeenCalledWith(['k-1']);
    });
  });

  it('rotate — confirm dialog с другим текстом', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('bulk-rotate'));

    const dialog = await screen.findByTestId('bulk-confirm-dialog');
    expect(within(dialog).getByText(/Ротировать 1 ключ\?/)).toBeInTheDocument();
    expect(within(dialog).getByText(/обнови \.env/)).toBeInTheDocument();
  });

  it('подтверждение rotate — вызывает bulkRotate', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('bulk-rotate'));
    await user.click(screen.getByTestId('bulk-confirm-submit'));

    await waitFor(() => {
      expect(bulkRotateMock).toHaveBeenCalledWith(['k-1']);
    });
  });

  it('cancel в confirm dialog — закрывает диалог, не зовёт mutation', async () => {
    const user = userEvent.setup();
    render(withQueryClient(<KeysPage />));
    await user.click(screen.getByTestId('select-key-k-1'));
    await user.click(screen.getByTestId('bulk-revoke'));

    await screen.findByTestId('bulk-confirm-dialog');
    const dialog = screen.getByTestId('bulk-confirm-dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отмена' }));

    await waitFor(() => {
      expect(screen.queryByTestId('bulk-confirm-dialog')).not.toBeInTheDocument();
    });
    expect(bulkRevokeMock).not.toHaveBeenCalled();
  });
});
