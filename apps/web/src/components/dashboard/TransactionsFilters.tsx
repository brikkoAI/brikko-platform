'use client';

import { Search, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { TransactionType } from '@/lib/types';

/**
 * Sprint 8 §3 — Search/filter bar для TransactionsTable.
 *
 * UX-обоснование:
 *   - Search input наверху (как в Stripe/Linear), потому что пользователь приходит
 *     с задачей «найти транзакцию №12345» 80% времени; фильтры — secondary path.
 *   - Date range через два <input type=date>, не date-picker от сторонней библиотеки —
 *     браузерный native input работает на mobile, не тащит ~30 КБ JS, и AT-friendly.
 *   - Type filter — checkbox-grid (4 типа), а не dropdown, потому что выбор multi
 *     и пользователь должен видеть ВСЕ опции сразу. Dropdown добавил бы лишний
 *     клик для частого «выбрать все кроме refund».
 *   - Amount range — простые text-поля min/max, не slider. Slider даст ложную точность;
 *     пользователь думает в круглых числах (1000 ₽, 10000 ₽).
 *   - Chips активных фильтров — между filter-bar и table, чтобы при свёрнутом
 *     filter-bar (раскрывается accordion'ом) пользователь видел что фильтры применены.
 */

export interface TransactionsFilterValue {
  search: string;
  fromDate: string; // 'YYYY-MM-DD' или ''
  toDate: string;
  kinds: TransactionType[];
  minAmountRub: string; // free-form input, parse при apply
  maxAmountRub: string;
}

export const EMPTY_FILTERS: TransactionsFilterValue = {
  search: '',
  fromDate: '',
  toDate: '',
  kinds: [],
  minAmountRub: '',
  maxAmountRub: '',
};

interface Props {
  value: TransactionsFilterValue;
  onChange: (next: TransactionsFilterValue) => void;
}

const KIND_LABELS: Record<TransactionType, string> = {
  topup: 'Пополнения',
  usage: 'Списания',
  subscription: 'Подписки',
  refund: 'Возвраты',
  welcome_credit: 'Welcome',
};

const FILTERABLE_KINDS: TransactionType[] = ['topup', 'usage', 'subscription', 'refund'];

/**
 * Debounced controlled input — useDebouncedSearchInput сохраняет local state
 * и пушит наверх только через 300ms paused-typing.
 */
function useDebouncedSync<T>(
  value: T,
  onChange: (next: T) => void,
  delayMs: number,
): [T, (next: T) => void] {
  const [local, setLocal] = useState<T>(value);
  const lastExternal = useRef(value);

  // Внешнее изменение (e.g. clearAll) — синкаем без debounce.
  useEffect(() => {
    if (value !== lastExternal.current) {
      lastExternal.current = value;
      setLocal(value);
    }
  }, [value]);

  useEffect(() => {
    if (local === lastExternal.current) return;
    const id = setTimeout(() => {
      lastExternal.current = local;
      onChange(local);
    }, delayMs);
    return () => clearTimeout(id);
  }, [local, onChange, delayMs]);

  return [local, setLocal];
}

export function TransactionsFilters({ value, onChange }: Props) {
  const searchId = useId();
  const fromId = useId();
  const toId = useId();
  const minAmountId = useId();
  const maxAmountId = useId();

  const [search, setSearch] = useDebouncedSync(value.search, (s) => onChange({ ...value, search: s }), 300);

  function toggleKind(kind: TransactionType): void {
    const next = value.kinds.includes(kind)
      ? value.kinds.filter((k) => k !== kind)
      : [...value.kinds, kind];
    onChange({ ...value, kinds: next });
  }

  return (
    <div
      className="flex flex-col gap-4 rounded-lg border border-gray-200 bg-white p-4"
      data-testid="transactions-filters"
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={searchId}>Поиск</Label>
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
            aria-hidden="true"
          />
          <Input
            id={searchId}
            type="search"
            value={search}
            placeholder="По описанию транзакции"
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            data-testid="transactions-search"
          />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={fromId}>С даты</Label>
          <Input
            id={fromId}
            type="date"
            value={value.fromDate}
            max={value.toDate || undefined}
            onChange={(e) => onChange({ ...value, fromDate: e.target.value })}
            data-testid="transactions-from"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={toId}>По дату</Label>
          <Input
            id={toId}
            type="date"
            value={value.toDate}
            min={value.fromDate || undefined}
            onChange={(e) => onChange({ ...value, toDate: e.target.value })}
            data-testid="transactions-to"
          />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={minAmountId}>Сумма от, ₽</Label>
          <Input
            id={minAmountId}
            type="number"
            inputMode="numeric"
            min={0}
            value={value.minAmountRub}
            onChange={(e) => onChange({ ...value, minAmountRub: e.target.value })}
            data-testid="transactions-min-amount"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={maxAmountId}>Сумма до, ₽</Label>
          <Input
            id={maxAmountId}
            type="number"
            inputMode="numeric"
            min={0}
            value={value.maxAmountRub}
            onChange={(e) => onChange({ ...value, maxAmountRub: e.target.value })}
            data-testid="transactions-max-amount"
          />
        </div>
      </div>

      <fieldset>
        <legend className="text-body-sm font-medium text-gray-700">Тип операции</legend>
        <div className="mt-2 flex flex-wrap gap-3">
          {FILTERABLE_KINDS.map((k) => {
            const checked = value.kinds.includes(k);
            return (
              <label
                key={k}
                className="flex items-center gap-2 text-body-sm text-gray-700"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                  checked={checked}
                  onChange={() => toggleKind(k)}
                  data-testid={`transactions-kind-${k}`}
                />
                {KIND_LABELS[k]}
              </label>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

interface ChipsProps {
  value: TransactionsFilterValue;
  onChange: (next: TransactionsFilterValue) => void;
}

/**
 * Chip'ы активных фильтров. Каждый — кнопка с X для убрать.
 *
 * UX-нота: рендерим только когда есть хотя бы 1 активный фильтр; скрываем чтобы
 * не занимать визуальное место в "пустом" состоянии.
 */
export function TransactionsActiveFilters({ value, onChange }: ChipsProps) {
  const chips: Array<{ key: string; label: string; onRemove: () => void }> = [];

  if (value.search) {
    chips.push({
      key: 'search',
      label: `Поиск: «${value.search}»`,
      onRemove: () => onChange({ ...value, search: '' }),
    });
  }
  if (value.fromDate || value.toDate) {
    chips.push({
      key: 'date',
      label: `Дата: ${value.fromDate || '...'}—${value.toDate || '...'}`,
      onRemove: () => onChange({ ...value, fromDate: '', toDate: '' }),
    });
  }
  if (value.minAmountRub || value.maxAmountRub) {
    chips.push({
      key: 'amount',
      label: `Сумма: ${value.minAmountRub || '0'}–${value.maxAmountRub || '∞'} ₽`,
      onRemove: () => onChange({ ...value, minAmountRub: '', maxAmountRub: '' }),
    });
  }
  for (const kind of value.kinds) {
    chips.push({
      key: `kind-${kind}`,
      label: `Тип: ${KIND_LABELS[kind]}`,
      onRemove: () => onChange({ ...value, kinds: value.kinds.filter((k) => k !== kind) }),
    });
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="transactions-active-filters">
      {chips.map((c) => (
        <Badge key={c.key} variant="brand" className="gap-1.5 pl-2 pr-1 py-1">
          <span>{c.label}</span>
          <button
            type="button"
            onClick={c.onRemove}
            aria-label={`Убрать фильтр: ${c.label}`}
            className="rounded p-0.5 hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-600"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </Badge>
      ))}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onChange(EMPTY_FILTERS)}
        data-testid="transactions-clear-filters"
      >
        Сбросить всё
      </Button>
    </div>
  );
}

/**
 * Конвертация TransactionsFilterValue → query params для useTransactions.
 */
export function filtersToQuery(
  v: TransactionsFilterValue,
  page: { limit: number; offset: number },
) {
  const minK = v.minAmountRub.trim() ? Math.round(Number(v.minAmountRub) * 100) : undefined;
  const maxK = v.maxAmountRub.trim() ? Math.round(Number(v.maxAmountRub) * 100) : undefined;
  return {
    from: v.fromDate || undefined,
    to: v.toDate || undefined,
    kind: v.kinds.length > 0 ? v.kinds : undefined,
    search: v.search.trim() || undefined,
    min_amount: Number.isFinite(minK) ? (minK as number | undefined) : undefined,
    max_amount: Number.isFinite(maxK) ? (maxK as number | undefined) : undefined,
    limit: page.limit,
    offset: page.offset,
  };
}
