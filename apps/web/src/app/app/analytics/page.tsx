'use client';

import { BarChart3, Activity, Server, Layers, AlertCircle } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import type { Route } from 'next';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { useAnalyticsSummary } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';
import { toKopecks } from '@/lib/types';
import type { AnalyticsDaily, AnalyticsBreakdownItem } from '@/lib/api';

/**
 * BrikkoLens — `/app/analytics` (Phase 5 #4 Sprint 2, 2026-05-09).
 *
 * Sub-dashboard для observability: KPI totals + daily-сериях + топ
 * моделей/провайдеров. Графики на чистом SVG (без recharts) — проект
 * grayscale, простой, и lib в 100KB не нужен.
 *
 * UX:
 *   - 4 KPI карты в шапке (Запросы / Стоимость / Avg latency / Error rate).
 *   - Range pickers: 7 / 14 / 30 дней (7-day default достаточно для MVP).
 *   - Bar chart запросов в день (масштаб от max).
 *   - Bar chart стоимости в день.
 *   - Top 5 моделей + top 5 провайдеров (две колонки).
 */

type RangePreset = 7 | 14 | 30;

export default function AnalyticsPage() {
  const [range, setRange] = useState<RangePreset>(7);

  const today = new Date();
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const fromDate = new Date(today);
  fromDate.setDate(today.getDate() - (range - 1));

  const params = { from: fmt(fromDate), to: fmt(today) };
  const summary = useAnalyticsSummary(params);

  const totals = summary.data?.totals;
  const isEmpty =
    !summary.isLoading &&
    summary.data !== undefined &&
    (totals?.requests ?? 0) === 0;

  const errorRate =
    totals && totals.requests > 0
      ? ((totals.errors / totals.requests) * 100).toFixed(1)
      : '0.0';

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <span className="inline-flex items-center gap-2 text-body-sm text-fg-muted">
          <BarChart3 className="h-4 w-4" aria-hidden="true" />
          BrikkoLens · Аналитика
        </span>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-h2 font-semibold tracking-tight text-fg-primary">
            Аналитика API
          </h1>
          <div className="flex items-center gap-2">
            <RangeButton active={range === 7} onClick={() => setRange(7)}>
              7 дней
            </RangeButton>
            <RangeButton active={range === 14} onClick={() => setRange(14)}>
              14 дней
            </RangeButton>
            <RangeButton active={range === 30} onClick={() => setRange(30)}>
              30 дней
            </RangeButton>
          </div>
        </div>
        <p className="max-w-2xl text-body text-fg-muted">
          Объём запросов, стоимость и latency за выбранный период. Ошибки,
          использование cache и tools — по моделям и провайдерам.
        </p>
      </header>

      {summary.isLoading && !summary.data ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      ) : isEmpty ? (
        <Card>
          <div className="p-12">
            <EmptyState
              icon={<BarChart3 className="h-10 w-10 text-gray-400" strokeWidth={1.5} />}
              title="Данных пока нет"
              description="Когда вы начнёте использовать API — здесь появится статистика по запросам, стоимости и latency. Можно начать с документации:"
              action={
                <Link href={'/docs/getting-started' as Route}>
                  <Button>Начать с документации</Button>
                </Link>
              }
            />
          </div>
        </Card>
      ) : (
        summary.data && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <KpiCard
                label="Запросы"
                value={totals!.requests.toLocaleString('ru-RU')}
                hint={
                  totals!.cache_hits > 0
                    ? `${totals!.cache_hits.toLocaleString('ru-RU')} с cache`
                    : undefined
                }
                icon={<Activity className="h-4 w-4" aria-hidden="true" />}
              />
              <KpiCard
                label="Стоимость"
                value={formatKopecks(toKopecks(totals!.cost_kop), {
                  forceFraction: true,
                })}
                hint={`${(totals!.prompt_tokens + totals!.completion_tokens).toLocaleString('ru-RU')} токенов`}
                icon={<Layers className="h-4 w-4" aria-hidden="true" />}
              />
              <KpiCard
                label="Avg latency"
                value={`${totals!.avg_latency_ms.toLocaleString('ru-RU')} мс`}
                hint={
                  totals!.p95_latency_ms > 0
                    ? `p95 ${totals!.p95_latency_ms.toLocaleString('ru-RU')} мс`
                    : undefined
                }
                icon={<Server className="h-4 w-4" aria-hidden="true" />}
              />
              <KpiCard
                label="Ошибки"
                value={`${errorRate}%`}
                hint={`${totals!.errors.toLocaleString('ru-RU')} запросов`}
                icon={<AlertCircle className="h-4 w-4" aria-hidden="true" />}
                tone={totals!.errors > 0 ? 'warn' : 'neutral'}
              />
            </div>

            <Card>
              <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
                <CardTitle className="text-body font-medium text-fg-primary">
                  Запросы по дням
                </CardTitle>
                <CardDescription className="text-body-sm text-fg-faint">
                  {summary.data.range_from} — {summary.data.range_to}
                </CardDescription>
              </div>
              <div className="px-5 py-4">
                <BarSeries
                  data={summary.data.daily}
                  field="requests"
                  formatValue={(v) => v.toLocaleString('ru-RU')}
                />
              </div>
            </Card>

            <Card>
              <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
                <CardTitle className="text-body font-medium text-fg-primary">
                  Стоимость по дням
                </CardTitle>
                <CardDescription className="text-body-sm text-fg-faint">
                  В рублях
                </CardDescription>
              </div>
              <div className="px-5 py-4">
                <BarSeries
                  data={summary.data.daily}
                  field="cost_kop"
                  formatValue={(v) =>
                    formatKopecks(toKopecks(v), { forceFraction: true })
                  }
                />
              </div>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <BreakdownCard
                title="Топ моделей"
                items={summary.data.by_model}
              />
              <BreakdownCard
                title="Топ провайдеров"
                items={summary.data.by_provider}
              />
            </div>
          </>
        )
      )}

      <CardDescription className="px-1 text-body-sm text-fg-faint">
        Хранение — 8 недель. Латентность p50/p95 доступна на серверной
        конфигурации с Postgres; на dev-окружении заполняется только avg.
      </CardDescription>
    </div>
  );
}

function RangeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'inline-flex h-8 items-center rounded-md px-3 text-body-sm transition-colors ' +
        (active
          ? 'bg-brand-600 text-white'
          : 'border border-gray-200 bg-white text-fg-primary hover:bg-gray-50')
      }
    >
      {children}
    </button>
  );
}

function KpiCard({
  label,
  value,
  hint,
  icon,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ReactNode;
  tone?: 'neutral' | 'warn';
}) {
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 text-body-sm text-fg-muted">
        {icon}
        {label}
      </div>
      <div
        className={
          'mt-2 text-h3 font-semibold tracking-tight ' +
          (tone === 'warn' ? 'text-amber-600' : 'text-fg-primary')
        }
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-body-sm text-fg-faint">{hint}</div>
      ) : null}
    </Card>
  );
}

function BarSeries({
  data,
  field,
  formatValue,
}: {
  data: AnalyticsDaily[];
  field: 'requests' | 'cost_kop';
  formatValue: (v: number) => string;
}) {
  const values = data.map((d) => d[field]);
  const max = Math.max(1, ...values);
  const total = values.reduce((sum, v) => sum + v, 0);

  // Высота svg задаётся через CSS, бар-высоты — относительные.
  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-32 items-end gap-1">
        {data.map((d) => {
          const value = d[field];
          const heightPct = (value / max) * 100;
          return (
            <div
              key={d.day}
              className="group relative flex-1"
              style={{ minWidth: 4 }}
            >
              <div
                className="rounded-sm bg-brand-600/80 transition-colors hover:bg-brand-700"
                style={{ height: `${Math.max(heightPct, value > 0 ? 2 : 0)}%` }}
                aria-label={`${d.day}: ${formatValue(value)}`}
              />
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-md bg-fg-primary px-2 py-1 text-body-sm text-bg-base group-hover:block">
                {d.day} · {formatValue(value)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-body-sm text-fg-faint">
        <span>{data[0]?.day ?? '—'}</span>
        <span>Всего: {formatValue(total)}</span>
        <span>{data[data.length - 1]?.day ?? '—'}</span>
      </div>
    </div>
  );
}

function BreakdownCard({
  title,
  items,
}: {
  title: string;
  items: AnalyticsBreakdownItem[];
}) {
  if (items.length === 0) {
    return (
      <Card className="p-5">
        <CardTitle className="mb-3 text-body font-medium text-fg-primary">
          {title}
        </CardTitle>
        <p className="text-body-sm text-fg-faint">Нет данных</p>
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <CardTitle className="mb-3 text-body font-medium text-fg-primary">
        {title}
      </CardTitle>
      <ul className="flex flex-col gap-3">
        {items.slice(0, 5).map((item) => (
          <li key={item.key}>
            <div className="flex items-center justify-between text-body-sm">
              <span className="font-mono text-fg-primary">{item.key}</span>
              <span className="text-fg-muted">
                {item.requests.toLocaleString('ru-RU')} ·{' '}
                {formatKopecks(toKopecks(item.cost_kop), {
                  forceFraction: true,
                })}
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-gray-100">
              <div
                className="h-full rounded-full bg-brand-600/80"
                style={{ width: `${Math.max(2, item.share * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
