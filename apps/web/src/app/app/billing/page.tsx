'use client';

import { Suspense, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { Sparkles } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { HelperTooltip } from '@/components/ui/helper-tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import { TopupCard } from '@/components/dashboard/TopupCard';
import { TransactionsTable } from '@/components/dashboard/TransactionsTable';
import { PeriodDocuments } from '@/components/dashboard/PeriodDocuments';
import { AutoRefillCard } from '@/components/dashboard/settings/AutoRefillCard';
import { useBalance, useTransactions } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';
import { toast } from '@/components/ui/toast';
import { track } from '@/lib/analytics';

function BillingContent() {
  const balance = useBalance();
  const tx = useTransactions({ limit: 50 });
  const params = useSearchParams();

  useEffect(() => {
    if (params.get('topup') === 'success') {
      const amount = params.get('amount');
      toast.success(amount ? `Платёж принят. Баланс пополнен на ${amount} ₽.` : 'Платёж принят');
      track('topup_completed', { amount: amount ?? 'unknown' });
    }
  }, [params]);

  /**
   * Sprint 12 §4 — welcome-бонус 200 ₽ badge.
   *
   * Показываем ТОЛЬКО если:
   *   1. Есть транзакция типа `welcome_credit` (бонус начислен).
   *   2. НЕТ ни одной transaction типа `topup` (юзер ещё не пополнял своими).
   *
   * Скрывается после первого пополнения автоматически — не нужен localStorage
   * флаг, источник истины — backend transactions. Это и есть «один раз»: пока
   * юзер не пополнит, badge видим; после — пропадает навсегда.
   */
  const txItems = tx.data?.items ?? [];
  const hasWelcomeCredit = txItems.some((t) => t.type === 'welcome_credit');
  const hasTopup = txItems.some((t) => t.type === 'topup');
  const showWelcomeBonusBadge = hasWelcomeCredit && !hasTopup;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="text-3xl font-semibold text-gray-900">Биллинг</h1>
        <p className="mt-2 text-body text-gray-500">
          Пополнения, история транзакций и чеки самозанятого.
        </p>
      </header>

      {showWelcomeBonusBadge ? (
        <div
          className="inline-flex items-center gap-2 self-start rounded-full border border-brand-200 bg-brand-50 px-3 py-1.5 text-body-sm font-medium text-brand-700"
          data-testid="welcome-bonus-badge"
        >
          <Sparkles className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
          У тебя 200 ₽ welcome-бонуса — хватит на ~1 000 запросов к GPT-5 mini.
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <div className="flex items-center gap-1.5">
            <CardDescription>Текущий баланс</CardDescription>
            <HelperTooltip content="За каждый запрос с баланса списывается стоимость токенов (вход + выход) по прайсу модели в рублях. Точная сумма видна в Usage." />
          </div>
          {balance.isLoading ? (
            <Skeleton className="mt-1 h-9 w-32" />
          ) : (
            <CardTitle className="text-3xl tabular-nums" data-testid="billing-balance">
              {balance.data
                ? formatKopecks(balance.data.balance_kopecks, { forceFraction: true })
                : '— ₽'}
            </CardTitle>
          )}
          <p className="mt-3 text-body-sm text-gray-500">
            Все тарифы — prepaid. При балансе 0 запросы вернут 402.{' '}
            <span className="inline-flex items-center gap-1">
              Welcome-бонус 200 ₽
              <HelperTooltip
                label="Что такое welcome 200 ₽"
                content="Разовый бонус новым аккаунтам. Хватает на ~1 000 запросов к GPT-5 mini или ~120 запросов к Claude Sonnet 4.6. Не выводится."
              />
            </span>
            .
          </p>
        </Card>

        <div className="md:col-span-2">
          <TopupCard />
        </div>
      </div>

      <AutoRefillCard />

      <PeriodDocuments />

      <section className="rounded-lg border border-gray-200 bg-white p-6">
        <header className="mb-4">
          <h2 className="text-lg font-semibold text-gray-900">История транзакций</h2>
          <p className="text-body-sm text-gray-500">
            Чек НПД доступен по ссылке в каждой строке — формируется автоматически
            после каждого пополнения через ЮKassa.
          </p>
        </header>
        <TransactionsTable withFilters />
      </section>
    </div>
  );
}

export default function BillingPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <BillingContent />
    </Suspense>
  );
}
