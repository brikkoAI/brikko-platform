'use client';

import { Skeleton } from '@/components/ui/skeleton';
import { TwoFactorCard } from '@/components/dashboard/settings/TwoFactorCard';
import { ActiveSessionsCard } from '@/components/dashboard/settings/ActiveSessionsCard';
import { ChangePasswordCard } from '@/components/dashboard/settings/ChangePasswordCard';
import { LinkedAccountsCard } from '@/components/dashboard/settings/LinkedAccountsCard';
import { CloseAccountCard } from '@/components/dashboard/settings/CloseAccountCard';
import { useAccount } from '@/lib/auth';

/**
 * Settings → Безопасность.
 *
 * Порядок секций — по «частоте использования и важности»:
 *   1. 2FA — самое значимое усиление security, ставим первым.
 *   2. Активные сессии — частая нужда «потерял ноут».
 *   3. Смена пароля — раз в полгода-год.
 *   4. Закрытие аккаунта — destructive, в самом низу.
 *
 * Layout (settings/layout.tsx) добавляет header + tabs + closure-banner.
 */

export default function SecurityPage() {
  const account = useAccount();

  if (account.isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  if (!account.data) return null;
  const closureScheduled = Boolean(account.data.closure_scheduled);

  return (
    <div className="flex flex-col gap-6">
      <TwoFactorCard />
      <LinkedAccountsCard />
      <ActiveSessionsCard />
      <ChangePasswordCard />
      <CloseAccountCard closureScheduled={closureScheduled} />
    </div>
  );
}
