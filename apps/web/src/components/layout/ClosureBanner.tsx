'use client';

import { usePathname } from 'next/navigation';
import { Banner } from '@/components/ui/banner';
import { Button } from '@/components/ui/button';
import { useAccount, useCancelClosure } from '@/lib/auth';
import { toast } from '@/components/ui/toast';
import { formatDate } from '@/lib/utils';

/**
 * Sticky-top banner для запланированного закрытия аккаунта.
 *
 * UX-обоснование:
 *  - Sticky-top (не toast, не модалка): пользователь должен видеть «у меня
 *    через 28 дней удалится аккаунт» в каждом маршруте dashboard'а — это
 *    «пожар-flag», постоянное напоминание + всегда есть Cancel-CTA в один клик.
 *  - Скрываем на `/app/settings/security` — там же лежит CloseAccountCard,
 *    который сам показывает scheduled-state и даёт ту же отмену; дублирование
 *    выглядит как баг.
 *  - Серверный AppLayout — async server-component → не может usePathname.
 *    Поэтому banner — отдельный client-only компонент.
 *
 * Спека: 02_Product/v1.5 — Sprint 7 frontend brief.
 */
export function ClosureBanner() {
  const pathname = usePathname();
  const account = useAccount();
  const cancelClosure = useCancelClosure();

  // На /app/settings/security банер избыточен — там CloseAccountCard сам показывает state.
  if (pathname?.startsWith('/app/settings/security')) return null;

  // Используем Sprint 7 поле, fallback — Sprint 6 legacy.
  const closureAt =
    account.data?.closure_scheduled_at ?? account.data?.scheduled_closure_at ?? null;
  if (!closureAt) return null;

  return (
    <div
      className="sticky top-0 z-30 -mx-6 mb-6 px-6 lg:-mx-8 lg:px-8"
      data-testid="closure-banner-sticky"
    >
      <Banner
        variant="warning"
        title={`Аккаунт будет закрыт ${formatDate(closureAt)}`}
        description="API-ключи продолжают работать до этой даты. Можно отменить закрытие в любой момент."
        action={
          <Button
            variant="secondary"
            size="sm"
            loading={cancelClosure.isPending}
            onClick={async () => {
              try {
                await cancelClosure.mutateAsync();
                toast.success('Закрытие аккаунта отменено.');
              } catch {
                toast.error('Не удалось отменить. Попробуй ещё раз.');
              }
            }}
            data-testid="closure-banner-cancel-button"
          >
            Отменить закрытие
          </Button>
        }
        data-testid="closure-banner"
      />
    </div>
  );
}
