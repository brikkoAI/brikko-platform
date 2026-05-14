'use client';

import { Suspense, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { ChevronDown, ChevronUp, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  PAYMENT_FAILURE_DESCRIPTION,
  PAYMENT_FAILURE_DETAIL,
  PAYMENT_FAILURE_TITLE,
  type PaymentFailureReason,
} from '@/lib/error-copy';

const ALL_REASONS: PaymentFailureReason[] = [
  'insufficient_funds',
  'card_declined',
  '3ds_failed',
  'card_expired',
  'network_error',
  'unknown',
];

function isReason(v: string | null): v is PaymentFailureReason {
  return v != null && (ALL_REASONS as string[]).includes(v);
}

/**
 * §5.9 Payment failed — страница, на которую редиректит ЮKassa при отказе.
 *
 * UX-обоснование:
 *   - Generic title + description вверху, конкретная причина — в раскрывающемся блоке.
 *     Юзер не всегда хочет читать «3-D Secure» — ему нужна одна кнопка «Попробовать снова».
 *   - Альтернативы — отдельный список под error: СБП, T-Pay, SberPay, поддержка.
 */
function PaymentFailureContent() {
  const params = useSearchParams();
  const rawReason = params.get('reason');
  const reason: PaymentFailureReason = isReason(rawReason) ? rawReason : 'unknown';
  const [showDetail, setShowDetail] = useState(false);

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6">
      <Card className="flex flex-col items-center gap-4 py-10 text-center">
        <XCircle className="h-12 w-12 text-error-600" strokeWidth={1.5} aria-hidden="true" />
        <h1 className="text-2xl font-semibold text-gray-900">{PAYMENT_FAILURE_TITLE}</h1>
        <p className="max-w-md text-body text-gray-500">{PAYMENT_FAILURE_DESCRIPTION}</p>

        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          <Button asChild>
            <Link href="/app/billing">Попробовать снова</Link>
          </Button>
          <Button asChild variant="secondary">
            <Link href="/app/billing">Выбрать другой способ оплаты</Link>
          </Button>
        </div>

        <button
          type="button"
          onClick={() => setShowDetail((v) => !v)}
          className="mt-2 inline-flex items-center gap-1 text-body-sm text-brand-600 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
          aria-expanded={showDetail}
          data-testid="payment-failure-toggle-detail"
        >
          {showDetail ? 'Скрыть' : 'Подробнее'}
          {showDetail ? (
            <ChevronUp className="h-4 w-4" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-4 w-4" aria-hidden="true" />
          )}
        </button>

        {showDetail ? (
          <div
            className="mt-2 max-w-md rounded-md border border-gray-200 bg-gray-50 p-4 text-left text-body-sm text-gray-700"
            data-testid="payment-failure-detail"
          >
            <p className="font-medium text-gray-900">Причина: {reason}</p>
            <p className="mt-1">{PAYMENT_FAILURE_DETAIL[reason]}</p>
          </div>
        ) : null}
      </Card>

      <Card>
        <h2 className="text-body font-medium text-gray-900">Альтернативные способы оплаты</h2>
        <ul className="mt-3 flex flex-col gap-2 text-body-sm text-gray-700">
          <li>
            <Link href="/app/billing" className="text-brand-600 hover:underline">
              Оплатить через СБП (другой банк)
            </Link>
          </li>
          <li>
            <Link href="/app/billing" className="text-brand-600 hover:underline">
              Оплатить через T-Pay или SberPay
            </Link>
          </li>
          <li>
            <a
              href="https://t.me/BrikkoAI_bot"
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-600 hover:underline"
            >
              Связаться с поддержкой через @BrikkoAI_bot
            </a>
          </li>
        </ul>
      </Card>
    </div>
  );
}

export default function PaymentFailurePage() {
  return (
    <Suspense fallback={<Skeleton className="mx-auto h-64 w-full max-w-xl" />}>
      <PaymentFailureContent />
    </Suspense>
  );
}
