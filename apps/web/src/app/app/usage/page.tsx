'use client';

import { BarChart3, CalendarOff, Terminal } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { UsageChart } from '@/components/dashboard/UsageChart';
import { UsageRoutingBadges } from '@/components/dashboard/UsageRoutingBadges';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useApiKeys, useUsage } from '@/lib/auth';
import { formatKopecks, formatTokens } from '@/lib/utils';

type Period = '7d' | '30d' | '90d';

function rangeFor(period: Period) {
  const days = period === '7d' ? 7 : period === '30d' ? 30 : 90;
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - (days - 1) * 86_400_000).toISOString().slice(0, 10);
  return { from, to };
}

export default function UsagePage() {
  const [period, setPeriod] = useState<Period>('30d');
  const range = useMemo(() => rangeFor(period), [period]);

  const byDay = useUsage({ ...range, group_by: 'day' });
  const byModel = useUsage({ ...range, group_by: 'model' });
  const keys = useApiKeys();

  const totals = byDay.data?.totals;
  const isEmpty = !byDay.isLoading && (totals?.cost_kop ?? 0) === 0;

  // Three-tier empty states (§1.2):
  //  1. Нет ни одного активного ключа → "Сначала создать API-ключ" (BarChart3)
  //  2. Ключ есть, запросов 0 ever → "Запустить первый запрос" (Terminal)
  //  3. Запросы есть, но за выбранный период — ноль → "За выбранный период данных нет" (CalendarOff)
  //
  // Sigil для tier-3: всегда-isEmpty + наличие ключа + period !== '90d'. Если юзер на 90d
  // и пусто — это всё-таки tier-2 (нет запросов вообще), не имеет смысла предлагать сбросить
  // фильтр.
  const activeKeys = useMemo(
    () => (keys.data ?? []).filter((k) => !k.revoked_at),
    [keys.data],
  );
  const hasKey = activeKeys.length > 0;
  const tier: 1 | 2 | 3 = !hasKey ? 1 : period === '90d' ? 2 : 3;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-gray-900">Расход</h1>
          <p className="mt-2 text-body text-gray-500">
            Как ты тратишь токены. Drill-down по моделям ниже.
          </p>
        </div>
        <div role="group" aria-label="Период" className="flex rounded-md border border-gray-300 bg-white p-1">
          {(['7d', '30d', '90d'] as const).map((p) => (
            <Button
              key={p}
              variant={p === period ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setPeriod(p)}
              data-testid={`usage-period-${p}`}
            >
              {p === '7d' ? '7 дней' : p === '30d' ? '30 дней' : '90 дней'}
            </Button>
          ))}
        </div>
      </header>

      {isEmpty ? (
        tier === 1 ? (
          // §1.2.1 — нет ни одного ключа.
          <EmptyState
            icon={<BarChart3 className="h-12 w-12" strokeWidth={1.5} />}
            title="Сначала создать API-ключ"
            description="После первого запроса здесь появятся графики расхода по дням и моделям."
            action={
              <Button asChild>
                <a href="/app/keys">Создать ключ</a>
              </Button>
            }
          />
        ) : tier === 2 ? (
          // §1.2.2 — welcome state: ключ есть, запросов 0.
          <EmptyState
            icon={<Terminal className="h-12 w-12" strokeWidth={1.5} />}
            title="Запустить первый запрос"
            description="Скопировать curl-сниппет ниже и выполнить в терминале. Данные обновятся в течение 30 секунд."
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button onClick={() => byDay.refetch()} data-testid="usage-refetch">
                  Обновить
                </Button>
                <Button asChild variant="ghost" size="sm">
                  <a href="/docs/quickstart">Открыть Quickstart</a>
                </Button>
              </div>
            }
          />
        ) : (
          // §1.2.3 — запросы есть, но за выбранный период — ноль.
          <EmptyState
            icon={<CalendarOff className="h-12 w-12" strokeWidth={1.5} />}
            title="За выбранный период данных нет"
            description="Сменить период в фильтре или сбросить его. Данные за всё время — на вкладке «90 дней»."
            action={
              <Button onClick={() => setPeriod('90d')} data-testid="usage-reset-period">
                Сбросить фильтр
              </Button>
            }
          />
        )
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardDescription>Расход за период</CardDescription>
              {byDay.isLoading ? (
                <Skeleton className="mt-1 h-9 w-24" />
              ) : (
                <CardTitle className="text-3xl tabular-nums">
                  {totals ? formatKopecks(totals.cost_kop) : '— ₽'}
                </CardTitle>
              )}
            </Card>
            <Card>
              <CardDescription>Токены входа</CardDescription>
              {byDay.isLoading ? (
                <Skeleton className="mt-1 h-9 w-24" />
              ) : (
                <CardTitle className="text-3xl tabular-nums">
                  {totals ? formatTokens(totals.tokens_in) : '—'}
                </CardTitle>
              )}
            </Card>
            <Card>
              <CardDescription>Токены выхода</CardDescription>
              {byDay.isLoading ? (
                <Skeleton className="mt-1 h-9 w-24" />
              ) : (
                <CardTitle className="text-3xl tabular-nums">
                  {totals ? formatTokens(totals.tokens_out) : '—'}
                </CardTitle>
              )}
            </Card>
          </div>

          <section className="rounded-lg border border-gray-200 bg-white p-6">
            <header className="mb-4">
              <h2 className="text-lg font-semibold text-gray-900">По дням</h2>
              <p className="text-body-sm text-gray-500">Стоимость в рублях; одна колонка = один день.</p>
            </header>
            {byDay.isLoading ? (
              <Skeleton className="h-[200px] w-full" />
            ) : (
              <UsageChart items={byDay.data?.items ?? []} />
            )}
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-6">
            <header className="mb-4">
              <h2 className="text-lg font-semibold text-gray-900">По моделям</h2>
              <p className="text-body-sm text-gray-500">
                На что уходит бюджет. Сортировка по убыванию стоимости.
              </p>
            </header>

            {byModel.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Модель</TableHead>
                    <TableHead>Routing</TableHead>
                    <TableHead className="text-right">Токены вход</TableHead>
                    <TableHead className="text-right">Токены выход</TableHead>
                    <TableHead className="text-right">Кэш</TableHead>
                    <TableHead className="text-right">Стоимость</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(byModel.data?.items ?? []).map((m) => (
                    <TableRow key={m.model ?? 'all'}>
                      <TableCell className="font-medium text-gray-900">
                        <code className="font-mono text-body-sm">{m.model ?? '—'}</code>
                      </TableCell>
                      <TableCell>
                        <UsageRoutingBadges item={m} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-gray-700">
                        {formatTokens(m.tokens_in)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-gray-700">
                        {formatTokens(m.tokens_out)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-gray-500">
                        {formatTokens(m.cached_tokens)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium text-gray-900">
                        {formatKopecks(m.cost_kop)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
