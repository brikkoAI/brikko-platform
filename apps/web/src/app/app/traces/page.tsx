'use client';

import { Activity, AlertCircle, ArrowDownToDot, BadgeCheck, Hash, Search, Server, X } from 'lucide-react';
import Link from 'next/link';
import { useState, useEffect } from 'react';
import type { Route } from 'next';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useTraces } from '@/lib/auth';
import { formatKopecks } from '@/lib/utils';
import type { TraceListItem, TracesQueryParams } from '@/lib/api';
import { toKopecks } from '@/lib/types';

/**
 * BrikkoLens — `/app/traces` (Phase 5 #4 Sprint 1в, 2026-05-09).
 *
 * Главный observability-экран клиента: видны последние запросы к API
 * с моделью, latency, cost, статусом. Это «commodity»-уровень — не
 * tariff-gated на чтении (см. CEO правило про commodity).
 *
 * Sprint 2 добавит /app/analytics с графиками; Sprint 3 — search/
 * filters/retention. Здесь MVP-минимум: список + статус-фильтр +
 * пагинация.
 */

const PAGE_SIZE = 50;

type StatusFilter = 'all' | 'ok' | 'error';
type ProviderFilter =
  | 'all'
  | 'openai'
  | 'anthropic'
  | 'sber'
  | 'yandex'
  | 'mistral'
  | 'deepseek';

const PROVIDER_LABELS: Record<ProviderFilter, string> = {
  all: 'Все провайдеры',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  sber: 'GigaChat',
  yandex: 'YandexGPT',
  mistral: 'Mistral',
  deepseek: 'DeepSeek',
};

export default function TracesPage() {
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [providerFilter, setProviderFilter] = useState<ProviderFilter>('all');
  const [onlyTools, setOnlyTools] = useState(false);
  const [onlyCache, setOnlyCache] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Debounce — query сдвигается через 300мс простоя.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearchQuery(searchInput.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const params: TracesQueryParams = {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    ...(statusFilter !== 'all' ? { status: statusFilter as 'ok' | 'error' } : {}),
    ...(providerFilter !== 'all' ? { provider: providerFilter } : {}),
    ...(onlyTools ? { only_with_tools: true } : {}),
    ...(onlyCache ? { only_with_cache: true } : {}),
    ...(searchQuery ? { q: searchQuery } : {}),
  };
  const traces = useTraces(params);

  const items = traces.data?.items ?? [];
  const total = traces.data?.total ?? 0;
  const isEmpty = !traces.isLoading && items.length === 0 && total === 0;
  const hasActiveFilter =
    statusFilter !== 'all' ||
    providerFilter !== 'all' ||
    onlyTools ||
    onlyCache ||
    !!searchQuery;
  const resetFilters = () => {
    setStatusFilter('all');
    setProviderFilter('all');
    setOnlyTools(false);
    setOnlyCache(false);
    setSearchInput('');
    setSearchQuery('');
    setPage(0);
  };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <span className="inline-flex items-center gap-2 text-body-sm text-fg-muted">
          <Activity className="h-4 w-4" aria-hidden="true" />
          BrikkoLens · Observability
        </span>
        <h1 className="text-h2 font-semibold tracking-tight text-fg-primary">
          Запросы к API
        </h1>
        <p className="max-w-2xl text-body text-fg-muted">
          Лог всех вызовов через ваш API-ключ за последние 8 недель: модель,
          провайдер, токены, стоимость, latency. Подробности любого запроса —
          по клику.
        </p>
      </header>

      <Card>
        <div className="flex flex-col gap-3 border-b border-gray-200 px-5 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-body font-medium text-fg-primary">
              {total > 0 ? `${total.toLocaleString('ru-RU')} запросов` : 'Запросов нет'}
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <FilterButton
                active={statusFilter === 'all'}
                onClick={() => {
                  setStatusFilter('all');
                  setPage(0);
                }}
              >
                Все
              </FilterButton>
              <FilterButton
                active={statusFilter === 'ok'}
                onClick={() => {
                  setStatusFilter('ok');
                  setPage(0);
                }}
              >
                Успешные
              </FilterButton>
              <FilterButton
                active={statusFilter === 'error'}
                onClick={() => {
                  setStatusFilter('error');
                  setPage(0);
                }}
              >
                С ошибкой
              </FilterButton>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[180px]">
              <Search
                className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-faint"
                aria-hidden="true"
              />
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Поиск по request_id, модели, ошибке…"
                className="pl-8"
                aria-label="Поиск по запросам"
              />
            </div>
            <select
              value={providerFilter}
              onChange={(e) => {
                setProviderFilter(e.target.value as ProviderFilter);
                setPage(0);
              }}
              className="h-9 rounded-md border border-gray-200 bg-white px-2 text-body-sm text-fg-primary"
              aria-label="Фильтр по провайдеру"
            >
              {(Object.keys(PROVIDER_LABELS) as ProviderFilter[]).map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p]}
                </option>
              ))}
            </select>
            <ToggleChip
              active={onlyTools}
              onClick={() => {
                setOnlyTools((v) => !v);
                setPage(0);
              }}
            >
              tools
            </ToggleChip>
            <ToggleChip
              active={onlyCache}
              onClick={() => {
                setOnlyCache((v) => !v);
                setPage(0);
              }}
            >
              cache
            </ToggleChip>
            {hasActiveFilter ? (
              <button
                type="button"
                onClick={resetFilters}
                className="inline-flex h-8 items-center gap-1 rounded-md border border-gray-200 px-2 text-body-sm text-fg-muted hover:bg-gray-50"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
                Сбросить
              </button>
            ) : null}
          </div>
        </div>

        {traces.isLoading && items.length === 0 ? (
          <div className="space-y-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isEmpty && !hasActiveFilter ? (
          <div className="p-12">
            <EmptyState
              icon={<Activity className="h-10 w-10 text-gray-400" strokeWidth={1.5} />}
              title="Запросов пока нет"
              description="Когда вы начнёте использовать API — здесь появятся вызовы. Можете попробовать прямо сейчас:"
              action={
                <Link href={'/docs/getting-started' as Route}>
                  <Button>Документация для старта</Button>
                </Link>
              }
            />
          </div>
        ) : items.length === 0 ? (
          <div className="p-12">
            <EmptyState
              icon={<Search className="h-10 w-10 text-gray-400" strokeWidth={1.5} />}
              title="Ничего не нашлось"
              description="По выбранным фильтрам нет запросов. Попробуйте изменить условия поиска."
              action={
                <Button variant="ghost" onClick={resetFilters}>
                  Сбросить фильтры
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-32">Время</TableHead>
                  <TableHead>Модель</TableHead>
                  <TableHead className="text-right">Токены</TableHead>
                  <TableHead className="text-right">Latency</TableHead>
                  <TableHead className="text-right">Стоимость</TableHead>
                  <TableHead>Статус</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((row) => (
                  <TraceRow key={row.id} row={row} />
                ))}
              </TableBody>
            </Table>
            <div className="flex items-center justify-between border-t border-gray-200 px-5 py-3 text-body-sm text-fg-muted">
              <span>
                Стр. {page + 1} ·{' '}
                {Math.min(page * PAGE_SIZE + items.length, total).toLocaleString('ru-RU')} из{' '}
                {total.toLocaleString('ru-RU')}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Назад
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!traces.data?.has_more}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Дальше
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>

      <CardDescription className="px-1 text-body-sm text-fg-faint">
        Хранение — 8 недель. Подробные тела запросов и ответов сохраняются только
        если включён prompt logging в{' '}
        <Link href={'/app/settings' as Route} className="underline">
          настройках
        </Link>
        . Данные изолированы по аккаунту, виден только ваш трафик.
      </CardDescription>
    </div>
  );
}

function FilterButton({
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

function ToggleChip({
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
        'inline-flex h-8 items-center rounded-md px-3 text-body-sm font-mono transition-colors ' +
        (active
          ? 'bg-fg-primary text-bg-base'
          : 'border border-gray-200 bg-white text-fg-muted hover:bg-gray-50')
      }
      aria-pressed={active}
    >
      {children}
    </button>
  );
}

function TraceRow({ row }: { row: TraceListItem }) {
  const time = new Date(row.finished_at);
  const timeLabel = time.toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const dateLabel = time.toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
  });

  return (
    <TableRow className="cursor-pointer hover:bg-gray-50">
      <TableCell className="text-body-sm text-fg-muted">
        <span className="font-mono">{timeLabel}</span>
        <br />
        <span className="text-body-sm text-fg-faint">{dateLabel}</span>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-2 font-medium text-fg-primary">
            <Server className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
            {row.model}
          </span>
          <span className="flex items-center gap-1 text-body-sm text-fg-muted">
            <Hash className="h-3 w-3" aria-hidden="true" />
            <span className="font-mono">{row.request_id.slice(0, 12)}…</span>
            {row.is_streaming ? <Badge variant="neutral">stream</Badge> : null}
            {row.tools_used ? <Badge variant="neutral">tools</Badge> : null}
            {row.cache_hit ? (
              <Badge variant="success" className="gap-1">
                <ArrowDownToDot className="h-3 w-3" />
                cache
              </Badge>
            ) : null}
            {row.pii_masked ? <Badge variant="info">PII</Badge> : null}
          </span>
        </div>
      </TableCell>
      <TableCell className="text-right text-body-sm font-mono tabular-nums">
        <span className="text-fg-primary">{row.prompt_tokens.toLocaleString('ru-RU')}</span>
        <span className="text-fg-faint"> + </span>
        <span className="text-fg-primary">{row.completion_tokens.toLocaleString('ru-RU')}</span>
      </TableCell>
      <TableCell className="text-right font-mono tabular-nums text-body-sm">
        {row.latency_ms.toLocaleString('ru-RU')} мс
      </TableCell>
      <TableCell className="text-right font-mono tabular-nums">
        {formatKopecks(toKopecks(row.cost_kop))}
      </TableCell>
      <TableCell>
        {row.status === 'ok' ? (
          <Badge variant="success" className="gap-1">
            <BadgeCheck className="h-3 w-3" />
            ОК
          </Badge>
        ) : (
          <Badge variant="error" className="gap-1">
            <AlertCircle className="h-3 w-3" />
            {row.status}
          </Badge>
        )}
      </TableCell>
    </TableRow>
  );
}
