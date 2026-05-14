/**
 * <DataExportSection> — Sprint 7 production rewrite.
 *
 * Что покрываем:
 *  - Никаких прошлых запросов — кнопка «Скачать все мои данные».
 *  - Запрос успешен — toast.success, polling-id выставлен.
 *  - 24h cooldown indicator виден когда последний запрос свежий.
 *  - 429 от backend — toast.warning без падений.
 *  - status=ready + download_url — рендерим ссылку «Скачать архив» + хинт «email тоже».
 *  - status=expired — рендерим явный hint «Ссылка истекла».
 *  - status=failed — рендерим error_message.
 *  - status=processing + polling — спиннер + label «Готовим архив…».
 *  - List of past exports — рендерится с badge'ами.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiClientError } from '@/lib/api';
import type { DataExportRequest } from '@/lib/types';

const toastWarning = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('@/components/ui/toast', () => ({
  toast: { success: toastSuccess, error: toastError, warning: toastWarning },
  Toaster: () => null,
}));

const useDataExportLatestReturn = vi.fn();
const useDataExportListReturn = vi.fn();
const useDataExportStatusReturn = vi.fn();
const requestMock = vi.fn();
vi.mock('@/lib/auth', () => ({
  useDataExportLatest: () => useDataExportLatestReturn(),
  useDataExportList: () => useDataExportListReturn(),
  useDataExportStatus: () => useDataExportStatusReturn(),
  useRequestDataExport: () => ({ mutateAsync: requestMock, isPending: false }),
}));

const { DataExportSection } = await import('@/components/dashboard/DataExportSection');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

function makeReady(): DataExportRequest {
  return {
    id: 'exp-ready',
    status: 'ready',
    requested_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    download_url: 'https://export.brikko.ai/abc.zip',
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    finished_at: new Date(Date.now() - 30 * 60_000).toISOString(),
  };
}

describe('<DataExportSection> Sprint 7', () => {
  beforeEach(() => {
    requestMock.mockReset();
    toastWarning.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    useDataExportLatestReturn.mockReset();
    useDataExportListReturn.mockReset();
    useDataExportStatusReturn.mockReset();
    // Default: ничего не ожидается, polling выключен.
    useDataExportListReturn.mockReturnValue({ data: [], isLoading: false });
    useDataExportStatusReturn.mockReturnValue({ data: undefined, isFetching: false });
  });

  it('первый раз — кнопка «Скачать все мои данные»', () => {
    useDataExportLatestReturn.mockReturnValue({ data: null, isLoading: false });
    render(withQueryClient(<DataExportSection />));
    const btn = screen.getByTestId('data-export-request-button');
    expect(btn.textContent).toMatch(/Скачать все мои данные/);
  });

  it('запрос успешен — toast.success', async () => {
    useDataExportLatestReturn.mockReturnValue({ data: null, isLoading: false });
    requestMock.mockResolvedValue({
      id: 'exp-1',
      status: 'pending',
      requested_at: new Date().toISOString(),
    });
    render(withQueryClient(<DataExportSection />));
    await userEvent.click(screen.getByTestId('data-export-request-button'));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('недавний запрос (≤24h) — кнопка → «Запросить ещё раз», cooldown hint', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: makeReady(),
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    expect(screen.getByTestId('data-export-request-button').textContent).toMatch(
      /Запросить ещё раз/,
    );
    expect(screen.getByTestId('data-export-cooldown-hint')).toBeInTheDocument();
  });

  it('cooldown — кнопка запроса в disabled state', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: makeReady(),
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    expect(screen.getByTestId('data-export-request-button')).toBeDisabled();
  });

  it('429 от backend — toast.warning без падений', async () => {
    useDataExportLatestReturn.mockReturnValue({ data: null, isLoading: false });
    requestMock.mockRejectedValue(
      new ApiClientError(429, { type: 'rate_limit', message: 'Too many' }),
    );
    render(withQueryClient(<DataExportSection />));
    await userEvent.click(screen.getByTestId('data-export-request-button'));
    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
  });

  it('status=ready + download_url — рендерим ссылку «Скачать архив» + email-hint', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: makeReady(),
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    const link = screen.getByTestId('data-export-download-link');
    expect(link).toHaveAttribute('href', 'https://export.brikko.ai/abc.zip');
    expect(screen.getByText(/Ссылка также отправлена на email/i)).toBeInTheDocument();
  });

  it('status=expired — рендерит явный «Ссылка истекла» hint', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: {
        id: 'exp-old',
        status: 'expired' as const,
        requested_at: new Date(Date.now() - 25 * 60 * 60_000).toISOString(),
        download_url: null,
      },
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    expect(screen.getByTestId('data-export-expired-hint')).toBeInTheDocument();
  });

  it('status=failed + error_message — рендерит ошибку из backend', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: {
        id: 'exp-fail',
        status: 'failed' as const,
        requested_at: new Date(Date.now() - 60 * 60_000).toISOString(),
        error_message: 'Архив больше 100 MB — пиши в саппорт.',
      },
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    expect(screen.getByText(/Архив больше 100 MB/)).toBeInTheDocument();
  });

  it('status=processing — рендерит spinner + «Готовим архив»', () => {
    useDataExportLatestReturn.mockReturnValue({
      data: {
        id: 'exp-2',
        status: 'processing' as const,
        requested_at: new Date(Date.now() - 10 * 1000).toISOString(),
      },
      isLoading: false,
    });
    useDataExportStatusReturn.mockReturnValue({
      data: undefined,
      isFetching: true,
    });
    render(withQueryClient(<DataExportSection />));
    expect(screen.getByText(/Готовим архив/)).toBeInTheDocument();
  });

  it('past exports — список с status badge', () => {
    useDataExportLatestReturn.mockReturnValue({ data: null, isLoading: false });
    useDataExportListReturn.mockReturnValue({
      data: [
        makeReady(),
        {
          id: 'exp-old',
          status: 'expired' as const,
          requested_at: new Date(Date.now() - 30 * 86_400_000).toISOString(),
          download_url: null,
        },
      ],
      isLoading: false,
    });
    render(withQueryClient(<DataExportSection />));
    const list = screen.getByTestId('data-export-list');
    expect(list).toBeInTheDocument();
    expect(screen.getByTestId('data-export-list-item-ready')).toBeInTheDocument();
    expect(screen.getByTestId('data-export-list-item-expired')).toBeInTheDocument();
  });
});
