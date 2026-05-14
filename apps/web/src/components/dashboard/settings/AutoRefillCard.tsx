'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { CreditCard, RefreshCw } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { Banner } from '@/components/ui/banner';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Field, FieldError, FieldHelper } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import { ApiClientError } from '@/lib/api';
import {
  useAutorefill,
  useDisableAutorefill,
  useUpdateAutorefill,
} from '@/lib/auth';
import { rubToKopecks, type AutorefillState, type Kopecks } from '@/lib/types';

/**
 * AutoRefillCard — настройка автопополнения баланса (Sprint 8 §1).
 *
 * UX-обоснование (почему один Card, не модалка):
 *   - Авто-пополнение — настройка с продолжительным состоянием (включил → забыл).
 *     Modal заставит каждый раз навигировать; inline-форма в Settings даёт визуальный
 *     якорь «вот текущее состояние, вот controls».
 *   - 3 поля (порог, сумма, карта) + toggle — небольшая форма, помещается без
 *     scroll'а; нет смысла прятать в drawer.
 *   - Banner для 3-failures-in-row — показываем ВНУТРИ карточки и над form,
 *     чтобы пользователь сразу видел что autorefill сейчас disabled и почему;
 *     CTA «Обновить карту» в банере — самый прямой path-of-recovery.
 *
 * Как failure_count ≥ 3 переключает banner:
 *   Backend сам disable'ит autorefill при 3 failed-charge подряд (см. BRIEF §1) —
 *   фронт получает enabled=false + failure_count=3. Банер рендерится по
 *   `state.failure_count >= 3`, не по `enabled` — чтобы остаться видимым даже
 *   после ручного disable пользователем (он мог нажать «Отключить», но факт
 *   проблемы с картой по-прежнему важен).
 */

// Схема — числа в рублях (frontend-friendly), конверсия в kopecks при submit.
const schema = z
  .object({
    threshold_rub: z
      .number({ invalid_type_error: 'Введи число' })
      .min(1, 'Минимум 1 ₽')
      .max(1_000_000, 'Слишком большое число'),
    amount_rub: z
      .number({ invalid_type_error: 'Введи число' })
      .min(100, 'Минимум 100 ₽')
      .max(1_000_000, 'Максимум 1 000 000 ₽'),
    payment_method_id: z.string().min(1, 'Выбери карту'),
  })
  .refine((d) => d.threshold_rub <= d.amount_rub, {
    path: ['threshold_rub'],
    message: 'Порог должен быть ≤ суммы пополнения',
  });

type FormValues = z.infer<typeof schema>;

export function AutoRefillCard() {
  const query = useAutorefill();
  const update = useUpdateAutorefill();
  const disable = useDisableAutorefill();

  if (query.isLoading) {
    return (
      <Card>
        <Skeleton className="h-7 w-48" />
        <Skeleton className="mt-4 h-32 w-full" />
      </Card>
    );
  }

  // 403 = тариф не поддерживает autorefill (PAYG). Backend возвращает
  // forbidden — это валидное состояние, не «ошибка», поэтому показываем
  // понятный upsell вместо красной плашки «обнови страницу» (баг 2026-05-02 #3).
  if (query.isError && query.error instanceof ApiClientError && query.error.status === 403) {
    return (
      <Card data-testid="autorefill-card-locked">
        <CardTitle>Авто-пополнение</CardTitle>
        <CardDescription className="mt-1">
          На текущем тарифе автопополнение недоступно. Перейди на Pro или выше — там
          можно настроить автосписание с карты, чтобы запросы не остановились
          при низком балансе.
        </CardDescription>
        <div className="mt-4">
          <Button asChild size="sm" variant="primary">
            <a href="/app/billing/upgrade">Сравнить тарифы</a>
          </Button>
        </div>
      </Card>
    );
  }

  // Нет данных (404 / network / SSR-bailout) — показываем «выключено»-state с
  // CTA, не ошибку: backend может ещё не реализовать endpoint, или у юзера
  // нет сохранённой карты — UX должен подсказать что делать дальше.
  if (!query.data) {
    return (
      <Card data-testid="autorefill-card-empty">
        <CardTitle>Авто-пополнение</CardTitle>
        <CardDescription className="mt-1">
          Авто-пополнение выключено. Включи, чтобы запросы не остановились при
          низком балансе — спишем сумму с сохранённой карты при пересечении порога.
        </CardDescription>
        <div className="mt-4">
          <Button asChild size="sm" variant="primary">
            <a href="/app/billing">Сначала пополнить и сохранить карту</a>
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card data-testid="autorefill-card">
      <AutoRefillContent
        state={query.data}
        onSave={(payload) => update.mutateAsync(payload)}
        saving={update.isPending}
        onDisable={() => disable.mutateAsync()}
        disabling={disable.isPending}
      />
    </Card>
  );
}

interface ContentProps {
  state: AutorefillState;
  onSave: (payload: {
    enabled: boolean;
    threshold_kopecks: Kopecks;
    amount_kopecks: Kopecks;
    payment_method_id: string;
  }) => Promise<AutorefillState>;
  saving: boolean;
  onDisable: () => Promise<AutorefillState>;
  disabling: boolean;
}

function AutoRefillContent({ state, onSave, saving, onDisable, disabling }: ContentProps) {
  const defaultMethodId =
    state.payment_method_id ??
    state.saved_methods.find((m) => m.is_default)?.id ??
    state.saved_methods[0]?.id ??
    '';

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isDirty },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      threshold_rub: state.threshold_kopecks ? Number(state.threshold_kopecks) / 100 : 500,
      amount_rub: state.amount_kopecks ? Number(state.amount_kopecks) / 100 : 5_000,
      payment_method_id: defaultMethodId,
    },
  });

  // Когда сервер возвращает свежий snapshot — переинициализируем форму, чтобы
  // isDirty правильно сбрасывался. Без этого «Сохранить» останется enabled.
  useEffect(() => {
    reset({
      threshold_rub: state.threshold_kopecks ? Number(state.threshold_kopecks) / 100 : 500,
      amount_rub: state.amount_kopecks ? Number(state.amount_kopecks) / 100 : 5_000,
      payment_method_id: state.payment_method_id ?? defaultMethodId,
    });
  }, [
    state.threshold_kopecks,
    state.amount_kopecks,
    state.payment_method_id,
    defaultMethodId,
    reset,
  ]);

  const hasFailures = state.failure_count >= 3;
  const noSavedMethods = state.saved_methods.length === 0;
  const selectedMethodId = watch('payment_method_id');

  async function submit(values: FormValues) {
    try {
      await onSave({
        enabled: true,
        threshold_kopecks: rubToKopecks(values.threshold_rub),
        amount_kopecks: rubToKopecks(values.amount_rub),
        payment_method_id: values.payment_method_id,
      });
      toast.success('Авто-пополнение сохранено');
    } catch {
      toast.error('Не удалось сохранить. Попробуй ещё раз.');
    }
  }

  async function handleDisable() {
    try {
      await onDisable();
      toast.success('Авто-пополнение отключено');
    } catch {
      toast.error('Не удалось отключить.');
    }
  }

  return (
    <>
      <CardTitle className="flex items-center gap-2">
        <RefreshCw className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
        Авто-пополнение
      </CardTitle>
      <CardDescription className="mt-1">
        Когда баланс падает ниже порога — Brikko автоматически списывает с карты
        указанную сумму через ЮKassa. Без него запросы остановятся при балансе 0 ₽.
      </CardDescription>

      {hasFailures ? (
        // Banner для 3+ failed-charge подряд — backend сам disable'ит, но мы
        // оставляем последний known-good state в полях, чтобы юзер не вводил заново.
        <Banner
          className="mt-4"
          variant="error"
          title={`Авто-пополнение отключено: ${state.failure_count} платежа подряд не прошло`}
          description={
            <>
              {state.last_failure_reason ?? 'Карта могла истечь или банк отклонил списание.'}{' '}
              Обнови карту через пополнение и включи авто-пополнение снова.
            </>
          }
          action={
            <Button asChild size="sm">
              <a href="/app/billing">Обновить карту</a>
            </Button>
          }
          data-testid="autorefill-failures-banner"
        />
      ) : !state.enabled ? (
        // Disabled state — call-to-action banner.
        <Banner
          className="mt-4"
          variant="info"
          title="Авто-пополнение выключено"
          description="Включи, чтобы запросы не остановились при низком балансе."
          data-testid="autorefill-disabled-banner"
        />
      ) : null}

      {noSavedMethods ? (
        <Banner
          className="mt-4"
          variant="warning"
          title="Сначала пополни баланс хотя бы раз"
          description="Карта сохранится при первом пополнении через ЮKassa, и тогда можно включить авто-пополнение."
          action={
            <Button asChild size="sm">
              <a href="/app/billing">Пополнить</a>
            </Button>
          }
          data-testid="autorefill-no-methods-banner"
        />
      ) : (
        <form
          className="mt-4 flex flex-col gap-4"
          onSubmit={handleSubmit(submit)}
          aria-label="Настройка авто-пополнения"
        >
          <Field>
            <Label htmlFor="autorefill-threshold">Когда баланс ниже, ₽</Label>
            <Input
              id="autorefill-threshold"
              type="number"
              min={1}
              step={1}
              invalid={Boolean(errors.threshold_rub)}
              aria-describedby="autorefill-threshold-help autorefill-threshold-error"
              {...register('threshold_rub', { valueAsNumber: true })}
              data-testid="autorefill-threshold"
            />
            <FieldHelper id="autorefill-threshold-help">
              Списание стартует при пересечении этого порога вниз. Рекомендуем 500 ₽.
            </FieldHelper>
            <FieldError id="autorefill-threshold-error">
              {errors.threshold_rub?.message}
            </FieldError>
          </Field>

          <Field>
            <Label htmlFor="autorefill-amount">Пополнить на, ₽</Label>
            <Input
              id="autorefill-amount"
              type="number"
              min={100}
              step={1}
              invalid={Boolean(errors.amount_rub)}
              aria-describedby="autorefill-amount-help autorefill-amount-error"
              {...register('amount_rub', { valueAsNumber: true })}
              data-testid="autorefill-amount"
            />
            <FieldHelper id="autorefill-amount-help">
              Минимум 100 ₽. Рекомендуем 5 000 ₽ — ~10 раз дольше работать без перезапуска.
            </FieldHelper>
            <FieldError id="autorefill-amount-error">{errors.amount_rub?.message}</FieldError>
          </Field>

          <Field>
            <Label htmlFor="autorefill-method">Карта</Label>
            <select
              id="autorefill-method"
              className="h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-body text-gray-900 hover:border-gray-400 focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/20"
              {...register('payment_method_id')}
              aria-describedby="autorefill-method-error"
              data-testid="autorefill-method"
            >
              {state.saved_methods.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.brand} {m.card_mask}
                  {m.is_default ? ' (по умолчанию)' : ''}
                </option>
              ))}
            </select>
            <FieldError id="autorefill-method-error">
              {errors.payment_method_id?.message}
            </FieldError>
          </Field>

          {selectedMethodId ? (
            <SelectedMethodSummary
              method={
                state.saved_methods.find((m) => m.id === selectedMethodId) ??
                state.saved_methods[0]
              }
            />
          ) : null}

          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              loading={saving}
              disabled={!isDirty && state.enabled}
              data-testid="autorefill-save"
            >
              {state.enabled ? 'Сохранить' : 'Включить авто-пополнение'}
            </Button>
            {state.enabled ? (
              <Button
                type="button"
                variant="ghost"
                loading={disabling}
                onClick={handleDisable}
                data-testid="autorefill-disable"
              >
                Отключить
              </Button>
            ) : null}
          </div>
        </form>
      )}
    </>
  );
}

function SelectedMethodSummary({
  method,
}: {
  method: AutorefillState['saved_methods'][number] | undefined;
}) {
  if (!method) return null;
  return (
    <div className="flex items-center gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-body-sm text-gray-700">
      <CreditCard className="h-4 w-4 text-gray-500" aria-hidden="true" />
      <span>
        Списания будут идти с <strong>{method.brand} {method.card_mask}</strong>. Карту можно
        заменить через ручное пополнение.
      </span>
    </div>
  );
}
