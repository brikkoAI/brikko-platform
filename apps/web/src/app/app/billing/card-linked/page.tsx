'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { CheckCircle2 } from 'lucide-react';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { track } from '@/lib/analytics';

/**
 * /app/billing/card-linked — landing после ЮKassa redirect (CEO 2026-05-15).
 *
 * Поток:
 *   1. CardLinkCard.UnlinkedState → POST /v1/billing/link-card
 *   2. window.location.href = confirmation_url (ЮKassa)
 *   3. ЮKassa проводит verification 1 ₽ → возврат на /app/billing/card-linked?status=success
 *   4. Эта страница показывает confirmation 3 секунды и автоматически redirect'ит
 *      на /app/billing — там CardLinkCard уже покажет linked state.
 *
 * UX-обоснование auto-redirect:
 *   - 3 секунды — достаточно чтобы юзер прочёл «карта привязана + 100 ₽
 *     начислены» и осознал результат, но не настолько долго, чтобы воспринять
 *     страницу как «надо что-то ещё сделать». Без auto-redirect юзер думает
 *     «я застрял, что дальше?» — это лишний step.
 *   - Делаем prefers-reduced-motion-safe: даже с reduced motion, redirect через
 *     setTimeout — не анимация, а навигация; обходить тут нечего.
 */

function CardLinkedContent() {
  const router = useRouter();
  const params = useSearchParams();
  const status = params.get('status');
  const success = status === 'success';

  useEffect(() => {
    if (!success) return;
    track('card_linked', { source: 'yookassa_redirect' });
    const t = window.setTimeout(() => {
      router.replace('/app/billing');
    }, 3000);
    return () => window.clearTimeout(t);
  }, [success, router]);

  if (!success) {
    return (
      <div className="mx-auto max-w-xl py-12">
        <Card data-testid="card-linked-failed">
          <CardTitle>Привязка не завершилась</CardTitle>
          <CardDescription className="mt-2">
            ЮKassa вернула статус «{status ?? 'неизвестно'}». Попробуй ещё раз
            или напиши{' '}
            <a href="mailto:hello@brikko.ru" className="underline">
              hello@brikko.ru
            </a>
            .
          </CardDescription>
          <div className="mt-4">
            <a
              href="/app/billing"
              className="inline-flex h-9 items-center justify-center rounded-md border border-gray-300 bg-white px-3 text-body-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Вернуться в биллинг
            </a>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-xl py-12">
      <Card data-testid="card-linked-success">
        <div className="flex items-start gap-3">
          <CheckCircle2
            className="h-6 w-6 flex-shrink-0 text-success-600"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          <div>
            <CardTitle>Карта привязана успешно</CardTitle>
            <CardDescription className="mt-2">
              +100 ₽ начислены на ваш баланс. Карта сохранена для будущей подписки.
            </CardDescription>
            <p className="mt-3 text-body-sm text-gray-500">
              Возвращаем в биллинг через 3 секунды…
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}

export default function CardLinkedPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <CardLinkedContent />
    </Suspense>
  );
}
