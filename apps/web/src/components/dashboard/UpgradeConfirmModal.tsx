'use client';

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Banner } from '@/components/ui/banner';
import { useChangeTariff } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';
import { formatKopecks } from '@/lib/utils';
import type { Kopecks, TariffChangeResponse, TariffSlug } from '@/lib/types';
import type { TariffCardData } from './TariffCard';

/**
 * Confirm-модалка апгрейда тарифа.
 *
 * UX-обоснование:
 *  - Modal, не drawer — выбор тарифа короткий (3-7 секунд), не нужно держать
 *    backdrop-context страницы с карточками. У drawer'а в этом случае больше
 *    «оверхеда» (фиксированная боковая панель), чем пользы.
 *  - 3 ветки: ОК (баланса хватает) / 402-error (показываем кнопку «Пополнить») /
 *    server-error (red banner + retry). Каждая ветка имеет primary CTA — пользователь
 *    никогда не остаётся в тупике.
 *  - При success — закрываем modal через onSuccess из родителя, чтобы родитель сам
 *    делал redirect (на /settings → Privacy для Privacy-тарифов, либо просто toast).
 *    Логика «куда после апгрейда» зависит от страницы, не от modal'а.
 */

interface UpgradeConfirmModalProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  tariff: TariffCardData | null;
  /** Текущий баланс в копейках — для проверки «хватает ли». */
  balanceKopecks: Kopecks;
  /** Цена месячной подписки в копейках. Для PAYG = 0. */
  priceKopecks: number;
  /** Колбэк после успешного апгрейда. Родитель решает redirect/toast. */
  onSuccess: (response: TariffChangeResponse) => void;
}

export function UpgradeConfirmModal({
  open,
  onOpenChange,
  tariff,
  balanceKopecks,
  priceKopecks,
  onSuccess,
}: UpgradeConfirmModalProps) {
  const changeTariff = useChangeTariff();
  const [errorState, setErrorState] = useState<
    | { kind: 'insufficient'; message: string }
    | { kind: 'server'; message: string }
    | null
  >(null);

  // Сброс ошибки при смене tariff'а или закрытии — иначе пользователь увидит старую красную
  // плашку при следующем апгрейде, что выглядит как «опять упало».
  useEffect(() => {
    if (!open) {
      setErrorState(null);
      changeTariff.reset();
    }
  }, [open, changeTariff]);

  const insufficient = useMemo(
    () => priceKopecks > 0 && balanceKopecks < priceKopecks,
    [priceKopecks, balanceKopecks],
  );

  if (!tariff) return null;

  async function onConfirm() {
    if (!tariff) return;
    setErrorState(null);
    try {
      const response = await changeTariff.mutateAsync(tariff.slug);
      onSuccess(response);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'insufficient_balance') {
        setErrorState({ kind: 'insufficient', message: err.message });
        return;
      }
      const message =
        err instanceof ApiClientError
          ? err.message
          : 'Не удалось сменить тариф. Попробуй ещё раз через минуту.';
      setErrorState({ kind: 'server', message });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-lg"
        aria-describedby="upgrade-confirm-desc"
        data-testid="upgrade-confirm-modal"
      >
        <DialogHeader>
          <DialogTitle>Перейти на «{tariff.name}»?</DialogTitle>
          <DialogDescription id="upgrade-confirm-desc">
            {priceKopecks > 0
              ? `Спишем ${formatKopecks(priceKopecks as Kopecks)} с баланса за первый месяц.`
              : 'Тариф бесплатный — переключение мгновенное.'}
          </DialogDescription>
        </DialogHeader>

        <dl className="mt-4 grid grid-cols-2 gap-3 rounded-md border border-gray-200 bg-gray-50 p-4">
          <div>
            <dt className="text-body-sm text-gray-500">Текущий баланс</dt>
            <dd className="text-body font-medium tabular-nums text-gray-900">
              {formatKopecks(balanceKopecks)}
            </dd>
          </div>
          <div>
            <dt className="text-body-sm text-gray-500">Стоимость подписки</dt>
            <dd className="text-body font-medium tabular-nums text-gray-900">
              {priceKopecks > 0 ? formatKopecks(priceKopecks as Kopecks) : '0 ₽'}
            </dd>
          </div>
        </dl>

        {errorState?.kind === 'insufficient' || (insufficient && !errorState) ? (
          <Banner
            variant="warning"
            className="mt-4"
            title="Недостаточно средств"
            description={
              errorState?.kind === 'insufficient'
                ? errorState.message
                : `На балансе ${formatKopecks(balanceKopecks)}, нужно ${formatKopecks(priceKopecks as Kopecks)}. Пополни баланс — тариф включится сразу после оплаты.`
            }
            action={
              <Button asChild size="sm">
                <a href="/app/billing">Пополнить</a>
              </Button>
            }
          />
        ) : null}

        {errorState?.kind === 'server' ? (
          <Banner
            variant="error"
            className="mt-4"
            title="Не получилось переключить тариф"
            description={errorState.message}
            action={
              <Button size="sm" variant="secondary" onClick={onConfirm} loading={changeTariff.isPending}>
                Повторить
              </Button>
            }
          />
        ) : null}

        {tariff.slug === 'pro_privacy' || tariff.slug === 'business_plus' ? (
          <div className="mt-4 flex items-start gap-2 rounded-md border border-info-50 bg-info-50/40 p-3">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-brand-700"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <p className="text-body-sm text-gray-700">
              После апгрейда вернёмся в Настройки → Privacy: нужно включить PII-маскинг,
              иначе ПДн пойдут в OpenAI без редакта.
            </p>
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={changeTariff.isPending}
          >
            Отмена
          </Button>
          <Button
            onClick={onConfirm}
            loading={changeTariff.isPending}
            disabled={insufficient && errorState?.kind !== 'server'}
            data-testid="upgrade-confirm-cta"
          >
            {priceKopecks > 0 ? 'Списать и переключить' : 'Переключить'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Helper, чтобы родителю не дублировать каталог цен — TariffCard.tsx экспортирует
// каталог, но цены там в виде строки для UI. Переводим строку «1 990 ₽/мес» в копейки —
// rumble. Лучше держать цены явно (см. PRICES_KOP в TariffCard.tsx? нет, не там).
// Решение: использую отдельный numeric mapping, синхронизированный с handlers.ts.
export const TARIFF_PRICE_KOP: Record<TariffSlug, number> = {
  payg: 0,
  pro_features: 199_000,
  pro_privacy: 279_000,
  team: 599_000,
  business: 1_999_000,
  business_plus: 10_000_000,
};
