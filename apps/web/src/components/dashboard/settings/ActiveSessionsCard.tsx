'use client';

import { Monitor, Smartphone, Globe } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useSessions, useRevokeSession, useRevokeAllSessions } from '@/lib/auth';
import { toast } from '@/components/ui/toast';
import { formatRelative } from '@/lib/utils';
import type { AuthSession } from '@/lib/types';

/**
 * Список активных auth-сессий.
 *
 * UX-обоснование:
 *  - Cards (а не table) — на мобилке табличная разметка ломается; cards адаптируются.
 *  - Текущая сессия отдельно с badge «Текущая» — defensively НЕ показываем кнопку
 *    «Завершить» для неё (это делается через выход).
 *  - «Завершить все остальные» — единый CTA для случая «потерял ноут, выкинуть всё кроме
 *    текущего браузера». Frequent-use-case у нас будет редкий, но если нужен — лучше
 *    одна кнопка, чем 5 индивидуальных revoke'ов.
 *  - Иконки: Monitor / Smartphone / Globe — детерминируются по device-string. Лучше
 *    «иконка по типу», чем generic-один-вид: пользователь visually парсит «это мой
 *    iPhone, это рабочий ноут».
 */

function deviceIcon(device: string) {
  const lower = device.toLowerCase();
  if (lower.includes('ios') || lower.includes('android') || lower.includes('mobile')) {
    return Smartphone;
  }
  if (lower.includes('windows') || lower.includes('mac') || lower.includes('linux')) {
    return Monitor;
  }
  return Globe;
}

export function ActiveSessionsCard() {
  const sessions = useSessions();
  const revoke = useRevokeSession();
  const revokeAll = useRevokeAllSessions();

  if (sessions.isLoading) {
    return (
      <Card>
        <CardTitle>Активные сессии</CardTitle>
        <Skeleton className="mt-4 h-32" />
      </Card>
    );
  }

  const list: AuthSession[] = sessions.data ?? [];
  const others = list.filter((s) => !s.is_current);
  const current = list.find((s) => s.is_current);

  return (
    <Card>
      <CardTitle>Активные сессии</CardTitle>
      <CardDescription className="mt-1">
        Где открыт твой аккаунт сейчас. Незнакомое устройство — сразу завершай.
      </CardDescription>

      <ul className="mt-4 flex flex-col gap-3" data-testid="sessions-list">
        {current ? <SessionItem session={current} disabled /> : null}
        {others.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            onRevoke={async () => {
              try {
                await revoke.mutateAsync(s.id);
                toast.success('Сессия завершена.');
              } catch {
                toast.error('Не удалось завершить сессию.');
              }
            }}
          />
        ))}
      </ul>

      {others.length > 0 ? (
        <Button
          variant="secondary"
          size="sm"
          className="mt-4"
          loading={revokeAll.isPending}
          onClick={async () => {
            try {
              await revokeAll.mutateAsync();
              toast.success('Все остальные сессии завершены.');
            } catch {
              toast.error('Не удалось завершить сессии.');
            }
          }}
          data-testid="revoke-all-sessions-button"
        >
          Завершить все остальные сессии
        </Button>
      ) : (
        <p className="mt-4 text-body-sm text-gray-500">Других сессий нет.</p>
      )}
    </Card>
  );
}

interface SessionItemProps {
  session: AuthSession;
  disabled?: boolean;
  onRevoke?: () => void;
}

function SessionItem({ session, disabled, onRevoke }: SessionItemProps) {
  const Icon = deviceIcon(session.device);
  return (
    <li
      className="flex items-start justify-between gap-3 rounded-md border border-gray-200 p-3"
      data-testid={`session-${session.id}`}
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-5 w-5 text-gray-500" strokeWidth={1.5} aria-hidden="true" />
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="text-body font-medium text-gray-900">{session.device}</span>
            {session.is_current ? <Badge variant="success">Текущая</Badge> : null}
          </div>
          <span className="text-body-sm text-gray-500">
            IP {session.ip} · {formatRelative(session.last_active_at)}
          </span>
        </div>
      </div>
      {!disabled && onRevoke ? (
        <Button variant="ghost" size="sm" onClick={onRevoke} aria-label={`Завершить сессию ${session.device}`}>
          Завершить
        </Button>
      ) : null}
    </li>
  );
}
