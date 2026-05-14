'use client';

import { MailPlus, Plus, Users, UserPlus, X } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { InviteSeatDialog } from '@/components/dashboard/InviteSeatDialog';
import { toast } from '@/components/ui/toast';
import { accountApi } from '@/lib/api';
import { useAccount, usePendingInvites, useRemoveSeat, useSeats } from '@/lib/auth';
import { formatDate } from '@/lib/utils';

export default function TeamPage() {
  const account = useAccount();
  const seats = useSeats();
  const invites = usePendingInvites();
  const removeSeat = useRemoveSeat();
  const [inviteOpen, setInviteOpen] = useState(false);

  const tariff = account.data?.tariff;
  const isPayg = tariff === 'payg';

  if (account.isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (isPayg) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <header>
          <h1 className="text-3xl font-semibold text-gray-900">Команда</h1>
          <p className="mt-2 text-body text-gray-500">
            Команды доступны на тарифе Pro и выше.
          </p>
        </header>
        <EmptyState
          icon={<UserPlus className="h-12 w-12" strokeWidth={1.5} />}
          title="Команды доступны на Pro и выше"
          description="Pro: 5 seats, 1 990 ₽/мес. Team: 15 seats, 4 990 ₽/мес. Общий баланс, общие ключи, общая аналитика."
          action={
            <Button asChild>
              <a href="/app/billing">Перейти на Pro</a>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-gray-900">Команда</h1>
          <p className="mt-2 text-body text-gray-500">
            Общий аккаунт. Каждый — со своими ключами, общим балансом и общей аналитикой.
          </p>
        </div>
        <Button leftIcon={<Plus className="h-4 w-4" />} onClick={() => setInviteOpen(true)} data-testid="invite-cta">
          Пригласить
        </Button>
      </header>

      <section className="rounded-lg border border-gray-200 bg-white p-6">
        <header className="mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Участники</h2>
        </header>
        {seats.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (seats.data?.length ?? 0) === 0 ? (
          // Linear/Stripe-style empty state: иконка + копирайт-инструкция + CTA.
          // Это первое, что видит owner после апгрейда — нужна явная подсказка
          // «следующий шаг», иначе пустая секция выглядит как баг.
          <EmptyState
            icon={<Users className="h-10 w-10" strokeWidth={1.5} />}
            title="Пока в команде только ты"
            description="Пригласи коллег по email — у каждого будут свои API-ключи и общий баланс."
            action={
              <Button leftIcon={<Plus className="h-4 w-4" />} onClick={() => setInviteOpen(true)}>
                Пригласить участника
              </Button>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>В команде с</TableHead>
                <TableHead className="text-right">Действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {seats.data!.map((s) => (
                <TableRow key={s.user_id}>
                  <TableCell className="font-medium text-gray-900">{s.email}</TableCell>
                  <TableCell>
                    {s.role === 'owner' ? (
                      <Badge variant="brand">Owner</Badge>
                    ) : s.role === 'admin' ? (
                      <Badge variant="info">Admin</Badge>
                    ) : (
                      <Badge>Member</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-body-sm text-gray-500">
                    {formatDate(s.joined_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    {s.role === 'owner' ? (
                      <span className="text-body-sm text-gray-400">—</span>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon={<X className="h-4 w-4" />}
                        onClick={async () => {
                          try {
                            await removeSeat.mutateAsync(s.user_id);
                            toast.success(`${s.email} удалён из команды.`);
                          } catch {
                            toast.error('Не удалось удалить участника.');
                          }
                        }}
                        aria-label={`Удалить ${s.email}`}
                      >
                        Удалить
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6">
        <header className="mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Pending приглашения</h2>
          <p className="text-body-sm text-gray-500">
            Ссылка действует 7 дней. После — нужно отправить заново.
          </p>
        </header>
        {invites.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (invites.data?.length ?? 0) === 0 ? (
          // Меньше визуального веса чем у seats-секции — это «подсекция»,
          // не главное действие. Нет CTA-кнопки (она наверху, в header'е).
          <div className="flex items-center gap-3 rounded-md bg-gray-50 px-4 py-3 text-body-sm text-gray-500">
            <MailPlus className="h-4 w-4 shrink-0 text-gray-400" strokeWidth={1.5} aria-hidden="true" />
            <span>
              Нет ожидающих приглашений. После «Пригласить» — здесь появится email и срок действия.
            </span>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>Отправлено</TableHead>
                <TableHead>Истекает</TableHead>
                <TableHead className="text-right">Действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invites.data!.map((i) => (
                <TableRow key={i.invite_id}>
                  <TableCell className="font-medium text-gray-900">{i.email}</TableCell>
                  <TableCell className="text-body-sm">{i.role === 'admin' ? 'Admin' : 'Member'}</TableCell>
                  <TableCell className="text-body-sm text-gray-500">
                    {formatDate(i.invited_at)}
                  </TableCell>
                  <TableCell className="text-body-sm text-gray-500">
                    {formatDate(i.expires_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        try {
                          await accountApi.cancelInvite(i.invite_id);
                          toast.success('Приглашение отозвано.');
                        } catch {
                          toast.error('Не удалось отозвать.');
                        }
                      }}
                    >
                      Отозвать
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <InviteSeatDialog open={inviteOpen} onOpenChange={setInviteOpen} />
    </div>
  );
}
