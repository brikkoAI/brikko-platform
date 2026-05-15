'use client';

import { useState } from 'react';
import { CreditCard, Loader2 } from 'lucide-react';
import { Banner } from '@/components/ui/banner';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';
import { useAutorefill, useLinkCard, useUnlinkCard } from '@/lib/auth';

/**
 * CardLinkCard — привязка карты (CEO 2026-05-15, subscription pivot).
 *
 * Состояния (определяются по `useAutorefill()`, потому что backend кладёт
 * привязанную карту в `saved_methods`, а её id — в `payment_method_id`):
 *
 *   1. Loading           — пока тянем autorefill snapshot.
 *   2. NOT linked        — saved_methods пуст. Показываем CTA «Привязать карту».
 *      Клик → POST /v1/billing/link-card → redirect на confirmation_url.
 *   3. Linked            — есть default saved_method. Показываем «VISA •••• 1234»
 *      + «Отвязать карту».
 *
 * UX-обоснование (почему один Card, а не модалка/drawer):
 *   - Card linking — состояние с продолжительной видимостью («что у меня с картой?»),
 *     а не workflow. Модалка для длительного state — антипаттерн (юзер закрыл —
 *     забыл; нужно открыть Settings, вспомнить путь). Inline-card в /app/billing
 *     даёт постоянную точку обзора.
 *   - Compliance-чекбокс перед action — обязателен для рекуррентных списаний
 *     ЮKassa (152-ФЗ + правила платёжной системы). Делаем checkbox в виде
 *     «Согласен с условиями автоплатежа», без него кнопка disabled. Не модалка
 *     терминаций — потому что одна short-form (1 поле + чекбокс + кнопка)
 *     укладывается в одном focal-take.
 *   - При ошибке backend (409 card_already_linked, 503 ЮKassa) — Banner
 *     внутри карточки, не toast. Ошибка privacy-чувствительна; пользователь
 *     должен видеть её рядом с действием, а не как уходящую плашку.
 *
 * Backend контракт (in-flight, см. lib/api.ts billingApi.linkCard):
 *   - POST /v1/billing/link-card { return_url } → { payment_id, confirmation_url }
 *   - После redirect ЮKassa → /app/billing/card-linked?status=success backend
 *     через webhook сохраняет payment_method_id + начисляет +100 ₽.
 *   - DELETE /v1/billing/card — отвязать. Endpoint backend сделает позже;
 *     пока кнопка опционально показывает «скоро» (см. props.unlinkEnabled).
 */

interface CardLinkCardProps {
  /**
   * Включить кнопку «Отвязать карту». Default false — пока backend endpoint
   * не готов. Когда BE добавит DELETE /v1/billing/card — переключаем в true.
   */
  unlinkEnabled?: boolean;
}

export function CardLinkCard({ unlinkEnabled = false }: CardLinkCardProps = {}) {
  const autorefill = useAutorefill();

  if (autorefill.isLoading) {
    return (
      <Card data-testid="card-link-loading">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-3 h-4 w-72" />
        <Skeleton className="mt-4 h-10 w-40" />
      </Card>
    );
  }

  // 403 (PAYG-tier без autorefill) трактуем как «нет привязанной карты» —
  // card-linking доступен на любом tier, а autorefill — только Pro+. Backend
  // эндпоинт /link-card отдельный от /autorefill (CEO 2026-05-15).
  const linkedMethod =
    autorefill.data?.saved_methods.find(
      (m) => m.id === autorefill.data?.payment_method_id,
    ) ??
    autorefill.data?.saved_methods.find((m) => m.is_default) ??
    autorefill.data?.saved_methods[0] ??
    null;

  if (linkedMethod) {
    return <LinkedState method={linkedMethod} unlinkEnabled={unlinkEnabled} />;
  }

  return <UnlinkedState />;
}

// ----------------------------------------------------------------
// State 1: карта НЕ привязана
// ----------------------------------------------------------------

function UnlinkedState() {
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const link = useLinkCard();

  async function handleLink() {
    setError(null);
    try {
      const returnUrl =
        typeof window !== 'undefined'
          ? `${window.location.origin}/app/billing/card-linked?status=success`
          : '/app/billing/card-linked?status=success';
      const res = await link.mutateAsync({ return_url: returnUrl });
      if (typeof window !== 'undefined') {
        window.location.href = res.confirmation_url;
      }
    } catch (e) {
      const message =
        e instanceof ApiClientError
          ? e.type === 'card_already_linked'
            ? 'Карта уже привязана. Обнови страницу.'
            : e.message
          : 'Не удалось начать привязку. Попробуй ещё раз.';
      setError(message);
    }
  }

  const submitting = link.isPending;

  return (
    <Card data-testid="card-link-unlinked">
      <CardTitle className="flex items-center gap-2">
        <CreditCard className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
        Привяжите карту — получите 100 ₽
      </CardTitle>
      <CardDescription className="mt-2">
        Бонус +100 ₽ welcome credit (≈ 5 000 запросов к GPT-5 mini). Карта
        сохранится для будущей подписки.
      </CardDescription>

      <ul className="mt-4 flex flex-col gap-2 text-body-sm text-gray-700">
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-gray-400" />
          Проверочный платёж 1 ₽ — сразу возврат на карту.
        </li>
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-gray-400" />
          Карта сохраняется для будущей подписки.
        </li>
        <li className="flex items-start gap-2">
          <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-gray-400" />
          +100 ₽ welcome credit (= 5 000 запросов).
        </li>
      </ul>

      {error ? (
        <Banner
          className="mt-4"
          variant="error"
          title="Не удалось начать привязку"
          description={error}
          data-testid="card-link-error"
        />
      ) : null}

      <label
        className="mt-5 flex items-start gap-2 text-body-sm text-gray-700"
        style={{ lineHeight: 1.5 }}
      >
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-600/30"
          checked={accepted}
          onChange={(e) => setAccepted(e.target.checked)}
          data-testid="card-link-consent"
          aria-describedby="card-link-consent-help"
        />
        <span id="card-link-consent-help">
          Согласен с условиями автоплатежа — ЮKassa сохранит карту для рекуррентных
          списаний (можно отвязать в любой момент).
        </span>
      </label>

      <div className="mt-4">
        <Button
          type="button"
          variant="primary"
          onClick={handleLink}
          disabled={!accepted || submitting}
          aria-disabled={!accepted || submitting || undefined}
          data-testid="card-link-submit"
        >
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Открываем ЮKassa
            </>
          ) : (
            'Привязать карту'
          )}
        </Button>
      </div>
    </Card>
  );
}

// ----------------------------------------------------------------
// State 2: карта привязана
// ----------------------------------------------------------------

interface LinkedStateProps {
  method: { brand: string; card_mask: string };
  unlinkEnabled: boolean;
}

function LinkedState({ method, unlinkEnabled }: LinkedStateProps) {
  const unlink = useUnlinkCard();

  async function handleUnlink() {
    try {
      await unlink.mutateAsync();
      toast.success('Карта отвязана');
    } catch (e) {
      const message =
        e instanceof ApiClientError
          ? e.message
          : 'Не удалось отвязать карту. Попробуй ещё раз.';
      toast.error(message);
    }
  }

  return (
    <Card data-testid="card-link-linked">
      <CardTitle className="flex items-center gap-2">
        <CreditCard className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
        Карта привязана: {method.brand} {method.card_mask}
      </CardTitle>
      <CardDescription className="mt-2">
        Будет использована для авто-продления вашей подписки. Списания идут только
        в день продления — единым счётом.
      </CardDescription>

      <div className="mt-4">
        <Button
          type="button"
          variant="secondary"
          onClick={handleUnlink}
          loading={unlink.isPending}
          disabled={!unlinkEnabled || unlink.isPending}
          aria-disabled={!unlinkEnabled || unlink.isPending || undefined}
          data-testid="card-link-unlink"
          title={
            unlinkEnabled
              ? undefined
              : 'Отвязка появится в ближайшем релизе. Напишите hello@brikko.ru, если нужно срочно.'
          }
        >
          Отвязать карту
        </Button>
      </div>
    </Card>
  );
}
