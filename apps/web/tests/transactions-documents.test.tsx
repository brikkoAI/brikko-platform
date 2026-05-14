/**
 * TransactionsTable: Comply Pack документы per-row (Sprint 4 / Поток O).
 *
 * Что покрываем:
 *   - Чек рендерится для транзакций с receipt_available.
 *   - Акт рендерится для transactions ≥1000₽ (AKT_THRESHOLD_KOP), кроме welcome_credit.
 *   - УПД рендерится только для крупных topup'ов (≥10 000₽).
 *   - Ссылки указывают на абсолютные API endpoints из billingApi.
 *   - Welcome — не показывает Акт даже если сумма выше порога (бухгалтерская семантика).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { toKopecks, type Transaction } from '@/lib/types';

vi.mock('@/lib/auth', async () => {
  const actual = await vi.importActual<typeof import('@/lib/auth')>('@/lib/auth');
  return {
    ...actual,
    useTransactions: () => ({
      data: { items: TX_FIXTURES, total: TX_FIXTURES.length },
      isLoading: false,
    }),
  };
});

const TX_FIXTURES: Transaction[] = [
  {
    id: 't-welcome',
    type: 'welcome_credit',
    amount_kopecks: toKopecks(20_000), // 200 ₽ — выше AKT-порога, но welcome не имеет акта.
    status: 'succeeded',
    description: 'Welcome',
    created_at: '2026-04-01T00:00:00Z',
    receipt_available: false,
  },
  {
    id: 't-topup-small',
    type: 'topup',
    amount_kopecks: toKopecks(50_000), // 500 ₽ — ниже AKT_THRESHOLD (1000 ₽).
    status: 'succeeded',
    description: 'Топап мелкий',
    created_at: '2026-04-15T00:00:00Z',
    receipt_available: true,
  },
  {
    id: 't-topup-medium',
    type: 'topup',
    amount_kopecks: toKopecks(500_000), // 5000 ₽ — есть Акт, нет УПД (порог 10 000).
    status: 'succeeded',
    description: 'Топап средний',
    created_at: '2026-04-20T00:00:00Z',
    receipt_available: true,
  },
  {
    id: 't-topup-large',
    type: 'topup',
    amount_kopecks: toKopecks(5_000_000), // 50 000 ₽ — Чек + Акт + УПД.
    status: 'succeeded',
    description: 'Топап крупный',
    created_at: '2026-04-25T00:00:00Z',
    receipt_available: true,
  },
];

const { TransactionsTable } = await import('@/components/dashboard/TransactionsTable');

function withQueryClient(node: ReactNode): ReactNode {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('<TransactionsTable> documents (Sprint 4)', () => {
  it('welcome — Акт не рендерится, даже при сумме выше порога', () => {
    render(withQueryClient(<TransactionsTable />));
    // welcome row не имеет ни чека, ни акта.
    const aktForWelcome = screen.queryAllByLabelText(
      /Скачать акт по транзакции t-welcome/i,
    );
    expect(aktForWelcome).toHaveLength(0);
  });

  it('topup 500 ₽ — только Чек, без Акта/УПД (ниже AKT-порога 1000 ₽)', () => {
    render(withQueryClient(<TransactionsTable />));
    expect(
      screen.getByLabelText(/Скачать чек по транзакции t-topup-small/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText(/Скачать акт по транзакции t-topup-small/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/Скачать УПД по транзакции t-topup-small/i),
    ).not.toBeInTheDocument();
  });

  it('topup 5000 ₽ — Чек + Акт; УПД нет (ниже UPD-порога 10 000 ₽)', () => {
    render(withQueryClient(<TransactionsTable />));
    expect(
      screen.getByLabelText(/Скачать чек по транзакции t-topup-medium/i),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(/Скачать акт по транзакции t-topup-medium/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText(/Скачать УПД по транзакции t-topup-medium/i),
    ).not.toBeInTheDocument();
  });

  it('topup 50 000 ₽ — все три документа: Чек, Акт, УПД', () => {
    render(withQueryClient(<TransactionsTable />));
    // asChild Button рендерит aria-label прямо на <a>, поэтому getByLabelText даёт сам anchor.
    const cheque = screen.getByLabelText(/Скачать чек по транзакции t-topup-large/i);
    const akt = screen.getByLabelText(/Скачать акт по транзакции t-topup-large/i);
    const upd = screen.getByLabelText(/Скачать УПД по транзакции t-topup-large/i);

    // URL'ы должны быть target=_blank (PDF в новой вкладке) + rel safety.
    for (const el of [cheque, akt, upd]) {
      expect(el.getAttribute('target')).toBe('_blank');
      expect(el.getAttribute('rel')).toBe('noopener noreferrer');
    }

    // Акт URL должен попадать в /billing/documents/{id}/akt.
    expect(akt.getAttribute('href') ?? '').toMatch(
      /\/billing\/documents\/t-topup-large\/akt$/,
    );

    // УПД URL — period_from=period_to=created_at.slice(0,10) согласно реализации.
    expect(upd.getAttribute('href') ?? '').toMatch(
      /\/billing\/documents\/upd\?period_from=2026-04-25&period_to=2026-04-25$/,
    );
  });
});
