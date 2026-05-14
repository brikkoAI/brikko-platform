'use client';

import Link from 'next/link';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { HelperTooltip } from '@/components/ui/helper-tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import { useBalance } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';

/**
 * Карточка баланса. Использует `useBalance` (refetchOnWindowFocus) — цифра свежая
 * после возврата с ЮKassa в другой вкладке.
 *
 * §4.2 — tooltip с объяснением списания за токены (вход + выход).
 */
export function BalanceCard() {
  const { data, isLoading } = useBalance();

  return (
    <Card>
      <div className="flex items-center gap-1.5">
        <CardDescription>Текущий баланс</CardDescription>
        <HelperTooltip content="За каждый запрос с баланса списывается стоимость токенов (вход + выход) по прайсу модели в рублях. Точная сумма видна в Usage." />
      </div>
      {isLoading ? (
        <Skeleton className="mt-1 h-9 w-32" />
      ) : (
        <CardTitle className="text-3xl tabular-nums">
          {data ? formatKopecks(data.balance_kopecks, { forceFraction: true }) : '— ₽'}
        </CardTitle>
      )}
      <Button asChild variant="primary" size="sm" className="mt-4">
        <Link href="/app/billing">Пополнить</Link>
      </Button>
    </Card>
  );
}
