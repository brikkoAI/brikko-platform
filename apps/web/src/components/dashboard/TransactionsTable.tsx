'use client';

import { ChevronLeft, ChevronRight, FileDown, FileText, Receipt, SearchX } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  EMPTY_FILTERS,
  filtersToQuery,
  TransactionsActiveFilters,
  TransactionsFilters,
  type TransactionsFilterValue,
} from '@/components/dashboard/TransactionsFilters';
import { billingApi } from '@/lib/api';
import { useTransactions } from '@/lib/auth';
import { formatDateTime, formatKopecks } from '@/lib/utils';
import {
  getDescription,
  getReceiptAvailable,
  getStatus,
} from '@/lib/transactions';
import type { Kopecks, Transaction, TransactionStatus, TransactionsQuery } from '@/lib/types';

/**
 * Порог для отображения «Акт» — 1000 ₽ (100 000 коп).
 * Ниже порога акт не оформляется (ФНС не требует), не нагружаем UI лишними ссылками.
 * Sprint 4 / Поток M согласовал backend-side.
 */
const AKT_THRESHOLD_KOP = 100_000;

/**
 * УПД (универсальный передаточный документ) релевантен только для крупных транзакций
 * (от 10 000 ₽ — порог НДС). Backend генерирует только для подходящих кейсов.
 */
const UPD_THRESHOLD_KOP = 1_000_000;

const PAGE_SIZE = 25;

const TYPE_LABEL: Record<Transaction['type'], string> = {
  topup: 'Пополнение',
  usage: 'Списание',
  subscription: 'Подписка',
  refund: 'Возврат',
  welcome_credit: 'Welcome',
};

function StatusBadge({ status }: { status: TransactionStatus }) {
  if (status === 'succeeded') return <Badge variant="success">Завершено</Badge>;
  if (status === 'pending') return <Badge variant="warning">В обработке</Badge>;
  return <Badge variant="error">Ошибка</Badge>;
}

interface TransactionsTableProps {
  /**
   * Если `true` — рендерим filter-bar и pagination (full Sprint 8 UX).
   * Если `false` (default) — backwards-compatible старый mode для legacy-страниц,
   * которые ещё не апгрейдились.
   *
   * Sprint 8: BillingPage передаёт `withFilters`. Tests могут передавать `false`,
   * чтобы тестировать только rendering rows.
   */
  withFilters?: boolean;
}

export function TransactionsTable({ withFilters = false }: TransactionsTableProps = {}) {
  const [filters, setFilters] = useState<TransactionsFilterValue>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);

  // useMemo чтобы query-key был стабильным; каждое изменение filters сбрасывает page → 0.
  const query: TransactionsQuery = useMemo(
    () =>
      withFilters
        ? (filtersToQuery(filters, {
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          }) as TransactionsQuery)
        : { limit: 50 },
    [withFilters, filters, page],
  );

  const { data, isLoading, isFetching } = useTransactions(query);

  function updateFilters(next: TransactionsFilterValue): void {
    setFilters(next);
    setPage(0);
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {withFilters ? <Skeleton className="h-32 w-full" /> : null}
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  const items = data?.items ?? [];
  const totalCount = data?.total_count ?? data?.total ?? items.length;
  const hasMore = data?.has_more ?? page * PAGE_SIZE + items.length < totalCount;

  // Активные фильтры? Если да — empty означает «по фильтру ничего», иначе — «нет операций».
  const hasActiveFilters =
    withFilters &&
    (filters.search.length > 0 ||
      filters.fromDate.length > 0 ||
      filters.toDate.length > 0 ||
      filters.minAmountRub.length > 0 ||
      filters.maxAmountRub.length > 0 ||
      filters.kinds.length > 0);

  return (
    <div className="flex flex-col gap-4">
      {withFilters ? (
        <>
          <TransactionsFilters value={filters} onChange={updateFilters} />
          <TransactionsActiveFilters value={filters} onChange={updateFilters} />
        </>
      ) : null}

      {items.length === 0 ? (
        hasActiveFilters ? (
          <EmptyState
            icon={<SearchX className="h-12 w-12" strokeWidth={1.5} />}
            title="Ничего не найдено"
            description="Под текущие фильтры подходящих транзакций нет. Попробуй сбросить или сменить даты."
            action={
              <Button variant="secondary" onClick={() => updateFilters(EMPTY_FILTERS)} data-testid="transactions-empty-clear">
                Сбросить фильтры
              </Button>
            }
          />
        ) : (
          // §1.3 — Transactions empty (новый аккаунт). welcome 200 ₽ упоминается явно.
          <EmptyState
            icon={<Receipt className="h-12 w-12" strokeWidth={1.5} />}
            title="Транзакций пока нет"
            description="Здесь появятся пополнения и списания за токены. На балансе уже 200 ₽ welcome-бонуса."
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <a
                  href="/app/billing"
                  className="inline-flex h-10 items-center justify-center rounded-md bg-brand-600 px-4 text-body font-medium text-white shadow-sm transition-colors hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                >
                  Пополнить баланс
                </a>
                <a
                  href="/docs/billing#welcome"
                  className="text-body-sm text-brand-600 hover:underline"
                >
                  Что такое welcome-бонус
                </a>
              </div>
            }
          />
        )
      ) : (
        <>
          <Table data-testid="transactions-table">
            <TableHeader>
              <TableRow>
                <TableHead>Дата</TableHead>
                <TableHead>Тип</TableHead>
                <TableHead>Описание</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead className="text-right">Сумма</TableHead>
                <TableHead className="text-right">Документы</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="text-body-sm text-gray-500">
                    {formatDateTime(t.created_at)}
                  </TableCell>
                  <TableCell>{TYPE_LABEL[t.type]}</TableCell>
                  <TableCell>{getDescription(t)}</TableCell>
                  <TableCell>
                    <StatusBadge status={getStatus(t)} />
                  </TableCell>
                  <TableCell
                    className={
                      'text-right tabular-nums ' +
                      (t.amount_kopecks >= 0 ? 'text-success-600 font-medium' : 'text-gray-700')
                    }
                  >
                    {t.amount_kopecks >= 0 ? '+' : ''}
                    {formatKopecks(t.amount_kopecks, { forceFraction: true })}
                  </TableCell>
                  <TableCell className="text-right">
                    <DocumentLinks tx={t} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {withFilters ? (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              showing={items.length}
              total={totalCount}
              hasMore={hasMore}
              isFetching={isFetching}
              onChange={setPage}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

function Pagination({
  page,
  pageSize,
  showing,
  total,
  hasMore,
  isFetching,
  onChange,
}: {
  page: number;
  pageSize: number;
  showing: number;
  total: number;
  hasMore: boolean;
  isFetching: boolean;
  onChange: (next: number) => void;
}) {
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = page * pageSize + showing;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-body-sm text-gray-500" aria-live="polite" data-testid="transactions-pagination-info">
        {total === 0 ? 'Нет операций' : `${start}–${end} из ${total}`}
        {isFetching ? ' · обновляем…' : ''}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<ChevronLeft className="h-4 w-4" />}
          disabled={page === 0}
          onClick={() => onChange(page - 1)}
          data-testid="transactions-prev"
        >
          Назад
        </Button>
        <Button
          variant="secondary"
          size="sm"
          rightIcon={<ChevronRight className="h-4 w-4" />}
          disabled={!hasMore}
          onClick={() => onChange(page + 1)}
          data-testid="transactions-next"
        >
          Дальше
        </Button>
      </div>
    </div>
  );
}

/**
 * Закрывающие документы по транзакции (Sprint 4 / Поток M coordination).
 *
 * UX-обоснование плотного отображения трёх ссылок (Чек / Акт / УПД):
 *   - Бухгалтерия пользователя нуждается в РАЗНЫХ документах под РАЗНЫЕ налоговые сценарии:
 *     чек самозанятого (ФНС-API), акт услуг (для ОСН/УСН), УПД (для НДС-обмена через ЭДО).
 *   - Группировать в один dropdown (как Stripe «Receipt» меню) — лишний клик; пользователь
 *     уже на странице «Документы», лучше показать сразу всё что есть.
 *   - Порог UPD_THRESHOLD_KOP = 1M коп (10 000 ₽) скрывает УПД для мелочи — это снимает
 *     визуальный шум; backend всё равно не сгенерирует УПД ниже порога.
 */
function DocumentLinks({ tx }: { tx: Transaction }) {
  const hasReceipt = getReceiptAvailable(tx);
  const absAmount: Kopecks = Math.abs(tx.amount_kopecks) as Kopecks;
  const showAkt = tx.type !== 'welcome_credit' && absAmount >= AKT_THRESHOLD_KOP;
  const showUpd = tx.type === 'topup' && absAmount >= UPD_THRESHOLD_KOP;

  if (!hasReceipt && !showAkt && !showUpd) {
    return <span className="text-body-sm text-gray-400">—</span>;
  }

  return (
    <div className="flex items-center justify-end gap-1">
      {hasReceipt ? (
        <DocLink
          href={billingApi.receiptUrl(tx.id)}
          icon={<FileDown className="h-4 w-4" />}
          ariaLabel={`Скачать чек по транзакции ${tx.id}`}
        >
          Чек
        </DocLink>
      ) : null}
      {showAkt ? (
        <DocLink
          href={billingApi.aktUrl(tx.id)}
          icon={<FileText className="h-4 w-4" />}
          ariaLabel={`Скачать акт по транзакции ${tx.id}`}
        >
          Акт
        </DocLink>
      ) : null}
      {showUpd ? (
        <DocLink
          href={billingApi.updUrl({
            // Однодневный диапазон — backend сам соберёт корректную дату.
            period_from: tx.created_at.slice(0, 10),
            period_to: tx.created_at.slice(0, 10),
          })}
          icon={<FileText className="h-4 w-4" />}
          ariaLabel={`Скачать УПД по транзакции ${tx.id}`}
        >
          УПД
        </DocLink>
      ) : null}
    </div>
  );
}

function DocLink({
  href,
  icon,
  ariaLabel,
  children,
}: {
  href: string;
  icon: React.ReactNode;
  ariaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Button asChild variant="ghost" size="sm" leftIcon={icon} aria-label={ariaLabel}>
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    </Button>
  );
}
