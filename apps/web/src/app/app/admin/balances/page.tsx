'use client';

/**
 * Provider balances admin page (2026-05-10).
 *
 * Контекст: соло-CEO Brikko хочет видеть в одном месте остатки на upstream-
 * аккаунтах LLM-провайдеров (OpenAI, Anthropic, DeepSeek, Sber, Moonshot,
 * MiniMax, Zhipu, Together, YandexGPT, Google). Часть провайдеров отдают
 * баланс через API (OpenAI, Anthropic), часть только через UI (Yandex/Sber)
 * — для них backend хранит manual-override + дату последнего top-up.
 *
 * Доступ: только email из ADMIN_EMAILS (env-список) — см. /app/status. Backend
 * вернёт 403 — мы покажем «Доступ запрещён». 401 ловится глобальным runRequest
 * и редиректит на /login.
 *
 * UX-обоснование:
 *   - Таблица (а не grid из карточек): CEO сравнивает runway между провайдерами,
 *     для сравнения нужны выровненные колонки; карточки 4×3 хуже.
 *   - Сортировка по runway asc по умолчанию: «что протухнет быстрее — наверху»,
 *     это primary-вопрос на этой странице. Можно переключить кликом на header.
 *   - Inline edit вместо Drawer/Modal: для одного поля (балансы) drawer перебор;
 *     pencil → 2 input'а в строке → save. Меньше context-switch.
 *   - «Пополнить» открывает topup-URL в новой вкладке: не теряем dashboard-state,
 *     CEO вернётся в эту же страницу и нажмёт «Обновить все».
 *   - Цветовая полоса справа от строки (не фон): фон ломается в dark theme и
 *     перебивает grayscale Cream Studio v6. Тонкая полоса работает в обеих темах.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import {
  AlertTriangle,
  Check,
  Cookie,
  ExternalLink,
  Pencil,
  RefreshCw,
  Wallet,
  X,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  type ApiClientError,
  type BalanceCurrency,
  type FetchMethod,
  type FetchStatus,
  fetchProviderBalances,
  type GetProviderBalancesResponse,
  type ManualBalanceUpdate,
  type ProviderBalance,
  type ProviderKey,
  refreshProviderBalances,
  setManualBalance,
} from '@/lib/api';
import { formatKopecks, formatRelative } from '@/lib/utils';
import { toKopecks } from '@/lib/types';
import { cn } from '@/lib/utils';

interface ProviderMetaEntry {
  name: string;
  topupUrl: string;
  /** Стартовая буква на «логотипе» (текстовый круглый аватар). */
  initial: string;
  /** Цвет фона аватара провайдера — узнаваемый brand. Все цвета вне фиолетового. */
  accent: string;
}

const PROVIDER_META: Record<ProviderKey, ProviderMetaEntry> = {
  openai: {
    name: 'OpenAI',
    topupUrl: 'https://platform.openai.com/account/billing',
    initial: 'O',
    accent: '#10a37f',
  },
  anthropic: {
    name: 'Anthropic',
    topupUrl: 'https://console.anthropic.com/settings/billing',
    initial: 'A',
    accent: '#d97757',
  },
  deepseek: {
    name: 'DeepSeek',
    topupUrl: 'https://platform.deepseek.com/usage',
    initial: 'D',
    accent: '#4d6bfe',
  },
  sber: {
    name: 'GigaChat (Sber)',
    topupUrl: 'https://developers.sber.ru/studio/workspaces',
    initial: 'G',
    accent: '#21a038',
  },
  moonshot: {
    name: 'Moonshot (Kimi)',
    topupUrl: 'https://platform.moonshot.cn/console/account',
    initial: 'M',
    accent: '#000000',
  },
  minimax: {
    name: 'MiniMax',
    topupUrl: 'https://platform.minimaxi.com/user-center/finance/charge',
    initial: 'X',
    accent: '#f15a24',
  },
  zhipu: {
    name: 'Zhipu (GLM)',
    topupUrl: 'https://open.bigmodel.cn/usercenter/financialcenter',
    initial: 'Z',
    accent: '#1f6feb',
  },
  together: {
    name: 'Together.ai',
    topupUrl: 'https://api.together.xyz/settings/billing',
    initial: 'T',
    accent: '#0f766e',
  },
  yandex: {
    name: 'YandexGPT',
    topupUrl: 'https://console.cloud.yandex.ru/billing',
    initial: 'Я',
    accent: '#fc3f1d',
  },
  google: {
    name: 'Google Gemini',
    topupUrl: 'https://console.cloud.google.com/billing',
    initial: 'G',
    accent: '#1a73e8',
  },
};

const CURRENCY_OPTIONS: BalanceCurrency[] = ['USD', 'RUB', 'CNY', 'EUR', 'tokens'];

const QK_BALANCES = ['admin-provider-balances'] as const;

// ============================================================
// Page
// ============================================================

export default function AdminBalancesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<ProviderKey | null>(null);

  const balances = useQuery({
    queryKey: QK_BALANCES,
    queryFn: fetchProviderBalances,
    staleTime: 30_000,
  });

  const refreshAll = useMutation({
    mutationFn: refreshProviderBalances,
    onSuccess: (data) => {
      qc.setQueryData<GetProviderBalancesResponse>(QK_BALANCES, data);
    },
  });

  // Loading state ----------------------------------------------------
  if (balances.isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader onRefresh={() => undefined} refreshing={false} disabled />
        <Card className="p-0">
          <Skeleton className="h-12 w-full rounded-none" />
          <div className="flex flex-col gap-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        </Card>
      </div>
    );
  }

  // Error state ------------------------------------------------------
  if (balances.isError) {
    const err = balances.error as ApiClientError;
    if (err.status === 403) {
      return (
        <Card className="p-12 text-center">
          <XCircle
            className="mx-auto mb-4 h-12 w-12 text-red-500"
            aria-hidden="true"
          />
          <CardTitle className="mb-2 text-h3">Доступ запрещён</CardTitle>
          <CardDescription className="text-body text-fg-muted">
            Эта страница доступна только администраторам платформы. Если вам
            нужен доступ — добавьте email в ADMIN_EMAILS на сервере.
          </CardDescription>
        </Card>
      );
    }
    return (
      <Card className="p-8 text-center">
        <AlertTriangle
          className="mx-auto mb-4 h-10 w-10 text-amber-500"
          aria-hidden="true"
        />
        <CardTitle className="mb-2 text-h4">
          Не удалось загрузить балансы
        </CardTitle>
        <CardDescription>{err.message ?? 'Неизвестная ошибка'}</CardDescription>
        <div className="mt-6 flex justify-center">
          <Button
            variant="secondary"
            onClick={() => balances.refetch()}
            leftIcon={<RefreshCw className="h-4 w-4" />}
          >
            Повторить
          </Button>
        </div>
      </Card>
    );
  }

  const items = balances.data?.items ?? [];
  // Empty state ------------------------------------------------------
  if (items.length === 0) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          onRefresh={() => refreshAll.mutate()}
          refreshing={refreshAll.isPending}
        />
        <Card className="p-12 text-center">
          <Wallet
            className="mx-auto mb-4 h-12 w-12 text-fg-muted"
            aria-hidden="true"
          />
          <CardTitle className="mb-2 text-h4">Пока нет данных</CardTitle>
          <CardDescription className="mx-auto max-w-md text-body text-fg-muted">
            Backend ещё не запросил балансы провайдеров. Нажмите «Обновить все»
            — система прогонит запросы по всем сконфигурированным API и подтянет
            свежие остатки.
          </CardDescription>
          <div className="mt-6 flex justify-center">
            <Button
              onClick={() => refreshAll.mutate()}
              loading={refreshAll.isPending}
              leftIcon={<RefreshCw className="h-4 w-4" />}
            >
              Обновить все
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  // Sort: runway_days asc (null/manual в конце — runway не считается).
  // Не делаем sortable header в первой версии: 95% времени CEO смотрит именно
  // «что закончится первым». Если потребуется — добавим click-to-sort позже.
  const sorted = sortByRunway(items);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        onRefresh={() => refreshAll.mutate()}
        refreshing={refreshAll.isPending}
      />

      {refreshAll.isError ? (
        <Card className="border-amber-200 bg-amber-50 p-4 text-body-sm text-amber-700">
          <span className="font-medium">Не удалось обновить:</span>{' '}
          {(refreshAll.error as ApiClientError | undefined)?.message ??
            'неизвестная ошибка'}
        </Card>
      ) : null}

      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-body">
            <thead className="bg-gray-50">
              <tr>
                <Th>Провайдер</Th>
                <Th align="right">Баланс</Th>
                <Th align="right">В рублях</Th>
                <Th align="right">Burn / день</Th>
                <Th align="right">Runway</Th>
                <Th>Проверено</Th>
                <Th>Источник</Th>
                <Th>Статус</Th>
                <Th align="right">Действия</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <BalanceRow
                  key={row.provider}
                  row={row}
                  isEditing={editing === row.provider}
                  onEditStart={() => setEditing(row.provider)}
                  onEditCancel={() => setEditing(null)}
                  onEditSaved={() => setEditing(null)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <CardDescription className="px-1 text-body-sm text-fg-faint">
        Цветовая отметка справа: красная — runway меньше 3 дней, жёлтая — меньше
        7. Считается из burn-rate последних 7 дней (backend). «Источник» = api
        (автоматический pull), manual (вписали руками), scrape (HTML-парсинг
        UI-кабинета провайдера).
      </CardDescription>
    </div>
  );
}

// ============================================================
// Header (shared между loading / loaded / empty)
// ============================================================

function PageHeader({
  onRefresh,
  refreshing,
  disabled = false,
}: {
  onRefresh: () => void;
  refreshing: boolean;
  disabled?: boolean;
}) {
  return (
    <header className="flex flex-col gap-3">
      <span className="inline-flex items-center gap-2 text-body-sm text-fg-muted">
        <Wallet className="h-4 w-4" aria-hidden="true" />
        Admin · Upstream balances
      </span>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="text-h2 font-semibold tracking-tight text-fg-primary">
            Балансы провайдеров
          </h1>
          <p className="max-w-2xl text-body text-fg-muted">
            Сколько денег осталось на upstream-аккаунтах LLM-провайдеров. Если
            какой-то API не отдаёт баланс — вписать вручную после top-up.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/app/admin/balances/cookies">
            <Button
              variant="secondary"
              leftIcon={<Cookie className="h-4 w-4" />}
            >
              Cookies скрейпера
            </Button>
          </Link>
          <Button
            onClick={onRefresh}
            loading={refreshing}
            disabled={disabled}
            leftIcon={<RefreshCw className="h-4 w-4" />}
          >
            Обновить все
          </Button>
        </div>
      </div>
    </header>
  );
}

// ============================================================
// Row
// ============================================================

interface RowProps {
  row: ProviderBalance;
  isEditing: boolean;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSaved: () => void;
}

function BalanceRow({
  row,
  isEditing,
  onEditStart,
  onEditCancel,
  onEditSaved,
}: RowProps) {
  const meta = PROVIDER_META[row.provider];
  const runwayBucket = bucketRunway(row.runway_days);
  // Цветовая полоса слева внутри строки — единая семантика runway.
  const runwayBarClass =
    runwayBucket === 'critical'
      ? 'bg-red-500'
      : runwayBucket === 'warning'
        ? 'bg-amber-500'
        : 'bg-transparent';

  return (
    <tr className="border-t border-gray-200 align-middle hover:bg-gray-50">
      {/* Provider — аватар + имя. Bar-маркер runway в самом начале row, чтобы
          в широком table он визуально привязывался к строке. */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <span
            className={cn('h-10 w-1 shrink-0 rounded-full', runwayBarClass)}
            aria-hidden="true"
          />
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-body-sm font-semibold text-white"
            style={{ backgroundColor: meta.accent }}
            aria-hidden="true"
          >
            {meta.initial}
          </span>
          <div className="flex flex-col">
            <span className="font-medium text-fg-primary">{meta.name}</span>
            {row.notes ? (
              <span className="text-body-sm text-fg-faint">{row.notes}</span>
            ) : null}
          </div>
        </div>
      </td>

      {/* Native balance / inline edit form */}
      {isEditing ? (
        <td colSpan={2} className="px-4 py-3">
          <InlineEditForm
            row={row}
            onCancel={onEditCancel}
            onSaved={onEditSaved}
          />
        </td>
      ) : (
        <>
          <td className="px-4 py-3 text-right font-mono text-fg-primary">
            {formatNative(row)}
          </td>
          <td className="px-4 py-3 text-right font-mono text-fg-primary">
            {row.balance_rub_kopecks !== null
              ? formatKopecks(toKopecks(row.balance_rub_kopecks), {
                  forceFraction: false,
                })
              : '—'}
          </td>
        </>
      )}

      <td className="px-4 py-3 text-right font-mono text-fg-muted">
        {row.burn_rate_kopecks_per_day !== null
          ? formatKopecks(toKopecks(row.burn_rate_kopecks_per_day))
          : '—'}
      </td>

      <td className="px-4 py-3 text-right font-mono">
        <RunwayCell runwayDays={row.runway_days} bucket={runwayBucket} />
      </td>

      <td className="px-4 py-3 text-body-sm text-fg-muted">
        {formatRelative(row.last_fetched_at)}
      </td>

      <td className="px-4 py-3">
        <SourceBadge method={row.fetch_method} />
      </td>

      <td className="px-4 py-3">
        <StatusBadge
          status={row.fetch_status}
          errorMessage={row.error_message}
        />
      </td>

      <td className="px-4 py-3 text-right">
        {!isEditing ? (
          <div className="flex items-center justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={onEditStart}
              aria-label={`Изменить баланс ${meta.name} вручную`}
              leftIcon={<Pencil className="h-3.5 w-3.5" />}
            >
              Править
            </Button>
            <a
              href={meta.topupUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-body-sm font-medium text-fg-muted transition-colors hover:bg-gray-100 hover:text-fg-primary"
              aria-label={`Открыть страницу пополнения ${meta.name} в новой вкладке`}
            >
              Пополнить
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

// ============================================================
// Inline edit form — manual override балансов
// ============================================================

function InlineEditForm({
  row,
  onCancel,
  onSaved,
}: {
  row: ProviderBalance;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [amount, setAmount] = useState<string>(
    row.balance_native !== null ? String(row.balance_native) : '',
  );
  const [currency, setCurrency] = useState<BalanceCurrency>(
    row.balance_currency ?? 'USD',
  );
  const [notes, setNotes] = useState<string>(row.notes ?? '');

  const save = useMutation({
    mutationFn: (payload: ManualBalanceUpdate) =>
      setManualBalance(row.provider, payload),
    onMutate: async (payload) => {
      // Оптимистичное обновление: показываем новое значение мгновенно.
      // На откат — снимок старого state.
      await qc.cancelQueries({ queryKey: QK_BALANCES });
      const previous =
        qc.getQueryData<GetProviderBalancesResponse>(QK_BALANCES);
      if (previous) {
        qc.setQueryData<GetProviderBalancesResponse>(QK_BALANCES, {
          items: previous.items.map((it) =>
            it.provider === row.provider
              ? {
                  ...it,
                  balance_native: payload.balance_native,
                  balance_currency: payload.currency,
                  fetch_status: 'manual',
                  fetch_method: 'manual',
                  last_fetched_at: new Date().toISOString(),
                  notes: payload.notes ?? it.notes,
                }
              : it,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData<GetProviderBalancesResponse>(QK_BALANCES, ctx.previous);
      }
    },
    onSuccess: (updated) => {
      // Server мог нормализовать значения (rub_kopecks, runway) — обновляем
      // строку точечно, не дёргая весь list-refetch (который в свою очередь
      // упёрся бы в rate-limited API провайдеров).
      const cur = qc.getQueryData<GetProviderBalancesResponse>(QK_BALANCES);
      if (cur) {
        qc.setQueryData<GetProviderBalancesResponse>(QK_BALANCES, {
          items: cur.items.map((it) =>
            it.provider === updated.provider ? updated : it,
          ),
        });
      }
      onSaved();
    },
  });

  const error = save.error as ApiClientError | null;
  const numeric = Number(amount.replace(',', '.'));
  const valid = Number.isFinite(numeric) && numeric >= 0;

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    save.mutate({
      balance_native: numeric,
      currency,
      notes: notes.trim() || undefined,
    });
  };

  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-wrap items-start gap-2"
      aria-label={`Ручное обновление баланса ${PROVIDER_META[row.provider].name}`}
    >
      <div className="flex flex-col gap-1">
        <label
          htmlFor={`amount-${row.provider}`}
          className="text-body-sm text-fg-muted"
        >
          Сумма
        </label>
        <Input
          id={`amount-${row.provider}`}
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          invalid={!valid}
          autoFocus
          required
          className="w-32"
          placeholder="50.25"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label
          htmlFor={`currency-${row.provider}`}
          className="text-body-sm text-fg-muted"
        >
          Валюта
        </label>
        <select
          id={`currency-${row.provider}`}
          value={currency}
          onChange={(e) => setCurrency(e.target.value as BalanceCurrency)}
          className="h-10 rounded-md border border-gray-300 bg-white px-3 text-body text-gray-900 hover:border-gray-400 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
        >
          {CURRENCY_OPTIONS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className="flex min-w-[180px] flex-1 flex-col gap-1">
        <label
          htmlFor={`notes-${row.provider}`}
          className="text-body-sm text-fg-muted"
        >
          Заметка
        </label>
        <Input
          id={`notes-${row.provider}`}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="top-up 10.05"
        />
      </div>
      <div className="flex gap-1 self-end">
        <Button
          type="submit"
          size="sm"
          disabled={!valid}
          loading={save.isPending}
          leftIcon={<Check className="h-3.5 w-3.5" />}
        >
          Сохранить
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onCancel}
          disabled={save.isPending}
          leftIcon={<X className="h-3.5 w-3.5" />}
        >
          Отмена
        </Button>
      </div>
      {error ? (
        <div
          role="alert"
          className="basis-full text-body-sm text-error-600"
        >
          {error.message}
        </div>
      ) : null}
    </form>
  );
}

// ============================================================
// Cells
// ============================================================

function RunwayCell({
  runwayDays,
  bucket,
}: {
  runwayDays: number | null;
  bucket: RunwayBucket;
}) {
  if (runwayDays === null) {
    return <span className="text-fg-faint">—</span>;
  }
  const colorClass =
    bucket === 'critical'
      ? 'text-red-500'
      : bucket === 'warning'
        ? 'text-amber-500'
        : 'text-fg-primary';
  return (
    <span className={cn('font-medium', colorClass)}>
      {Math.round(runwayDays)} дн
    </span>
  );
}

function StatusBadge({
  status,
  errorMessage,
}: {
  status: FetchStatus;
  errorMessage: string | null;
}) {
  switch (status) {
    case 'ok':
      return <Badge variant="success">ok</Badge>;
    case 'error':
      return (
        <Badge
          variant="error"
          title={errorMessage ?? undefined}
          aria-label={
            errorMessage ? `Ошибка: ${errorMessage}` : 'Ошибка получения'
          }
        >
          error
        </Badge>
      );
    case 'manual':
      return <Badge variant="brand">manual</Badge>;
    case 'pending':
      return <Badge variant="neutral">pending</Badge>;
  }
}

function SourceBadge({ method }: { method: FetchMethod }) {
  const label =
    method === 'api' ? 'API' : method === 'manual' ? 'Вручную' : 'Скрейп';
  return (
    <span className="text-body-sm text-fg-muted" data-source={method}>
      {label}
    </span>
  );
}

function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      className={cn(
        'h-10 px-4 align-middle text-body-sm font-medium uppercase tracking-wide text-gray-500',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    >
      {children}
    </th>
  );
}

// ============================================================
// Helpers
// ============================================================

type RunwayBucket = 'critical' | 'warning' | 'normal' | 'unknown';

function bucketRunway(runwayDays: number | null): RunwayBucket {
  if (runwayDays === null) return 'unknown';
  if (runwayDays < 3) return 'critical';
  if (runwayDays < 7) return 'warning';
  return 'normal';
}

function sortByRunway(items: ProviderBalance[]): ProviderBalance[] {
  return [...items].sort((a, b) => {
    // null runway уезжает в конец: либо нет данных (никогда не считали),
    // либо manual без burn-rate — оба варианта не относятся к «горящему».
    if (a.runway_days === null && b.runway_days === null) return 0;
    if (a.runway_days === null) return 1;
    if (b.runway_days === null) return -1;
    return a.runway_days - b.runway_days;
  });
}

function formatNative(row: ProviderBalance): string {
  if (row.balance_native === null || row.balance_currency === null) {
    return '—';
  }
  if (row.balance_currency === 'tokens') {
    return `${row.balance_native.toLocaleString('ru-RU')} tok`;
  }
  // Native balance может быть как с дробной частью (USD 50.25), так и целым
  // (RUB 12500). Используем 2 знака если дробь есть, иначе округляем.
  const hasFraction = row.balance_native % 1 !== 0;
  const formatted = new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: hasFraction ? 2 : 0,
    maximumFractionDigits: 2,
  }).format(row.balance_native);
  return `${formatted} ${row.balance_currency}`;
}
