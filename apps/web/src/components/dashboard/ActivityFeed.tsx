'use client';

import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  CreditCard,
  FileArchive,
  Key,
  KeyRound,
  LogIn,
  RefreshCcw,
  ShieldCheck,
  ShieldOff,
  UserMinus,
  UserPlus,
  UsersRound,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import { useState } from 'react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useActivity } from '@/lib/auth';
import { formatRelative } from '@/lib/utils';
import type { ActivityEvent, ActivityEventType } from '@/lib/types';

/**
 * Activity feed widget (Sprint 8 §4) — последние 10 событий аккаунта.
 *
 * UX-обоснование:
 *   - Размещаем под BalanceCard (на /app), не сбоку, потому что пользовательский
 *     путь читать сверху вниз; activity — secondary info, BalanceCard — primary.
 *   - 10 events дефолт. Не «бесконечная лента» — на /app пользователь не
 *     листает прошлое, ему нужны последние действия. Полная история (если
 *     понадобится позже) — отдельной /app/activity страницей.
 *   - Раскрываем details клик-по-row toggle (а не modal), потому что:
 *       • details — короткие текстовые блоки (1-3 предложения), modal был бы overkill.
 *       • Toggle сохраняет контекст списка — пользователь видит what's next.
 *   - Иконки лидируют по типу (Stripe-style); цвет иконки (default gray) не сигнализирует
 *     ничего особого — за исключением warning/error (балансовые алерты).
 */

const ICON_MAP: Record<ActivityEventType, LucideIcon> = {
  key_created: Key,
  key_revoked: KeyRound,
  key_rotated: RefreshCcw,
  balance_topup: ArrowDownToLine,
  balance_low: AlertTriangle,
  subscription_charged: CreditCard,
  tariff_changed: CreditCard,
  seat_invited: UserPlus,
  seat_joined: UsersRound,
  seat_removed: UserMinus,
  login_new_device: LogIn,
  two_factor_enabled: ShieldCheck,
  two_factor_disabled: ShieldOff,
  autorefill_charged: RefreshCcw,
  autorefill_failed: XCircle,
  data_export_requested: FileArchive,
  data_export_ready: FileArchive,
  unknown: Activity,
};

const WARN_TYPES = new Set<ActivityEventType>([
  'balance_low',
  'autorefill_failed',
  'two_factor_disabled',
]);

function iconForActivity(type: ActivityEventType): LucideIcon {
  return ICON_MAP[type] ?? ICON_MAP.unknown;
}

export function ActivityFeed() {
  const { data, isLoading, isError } = useActivity(10);

  if (isLoading) {
    return (
      <Card data-testid="activity-feed-loading">
        <CardTitle>Последние события</CardTitle>
        <div className="mt-4 flex flex-col gap-3">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardTitle>Последние события</CardTitle>
        <CardDescription className="mt-2">
          Не удалось загрузить события. Обнови страницу.
        </CardDescription>
      </Card>
    );
  }

  const events = data ?? [];

  return (
    <Card data-testid="activity-feed">
      <div className="flex items-center justify-between">
        <CardTitle>Последние события</CardTitle>
        <span className="text-body-sm text-gray-500">10 шт.</span>
      </div>

      {events.length === 0 ? (
        <p className="mt-4 text-body-sm text-gray-500">
          Пока ничего не происходило. После первого ключа или пополнения здесь появятся события.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-gray-200" role="list">
          {events.map((e) => (
            <ActivityItem key={e.id} event={e} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ActivityItem({ event }: { event: ActivityEvent }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = iconForActivity(event.type);
  const isWarn = WARN_TYPES.has(event.type);
  const hasDetails = Boolean(event.details);

  return (
    <li className="py-3" data-testid={`activity-item-${event.id}`}>
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((v) => !v)}
        className="flex w-full items-start gap-3 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 disabled:cursor-default"
        disabled={!hasDetails}
        aria-expanded={hasDetails ? expanded : undefined}
        aria-controls={hasDetails ? `activity-details-${event.id}` : undefined}
      >
        <span
          className={
            'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ' +
            (isWarn ? 'bg-warning-50 text-warning-600' : 'bg-gray-100 text-gray-600')
          }
        >
          <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-body text-gray-900">{event.summary}</p>
          <p className="text-body-sm text-gray-500">{formatRelative(event.created_at)}</p>
        </div>
      </button>
      {hasDetails && expanded ? (
        <div
          id={`activity-details-${event.id}`}
          className="mt-2 ml-11 rounded-md bg-gray-50 p-3 text-body-sm text-gray-700"
        >
          {event.details}
        </div>
      ) : null}
    </li>
  );
}
