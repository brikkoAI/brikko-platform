'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Field, FieldError, FieldHelper } from '@/components/ui/form';
import { toast } from '@/components/ui/toast';
import { useTopup } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';

const PRESETS = [500, 1_000, 5_000, 10_000];

// CEO 30.04: минимум 100 ₽ за пополнение, чтобы не перекрывать «попробовать
// после welcome-бонуса» сценарий — когда юзер потратил 200 ₽ и хочет докинуть
// 100-200 ₽ для дальнейших экспериментов, не отдавая сразу 500. Просадка по
// fees ЮKassa (5% от 100 = 5 ₽) комментируется в 16_ceo_decisions.
const MIN_TOPUP_RUB = 100;

// 2026-05-08: ЮKassa одобрила 4 метода для шопа 1345959
// (карты + СБП + T-Pay + SberPay). 'auto' = передаём undefined в API,
// ЮKassa сама показывает picker (legacy). Любой другой = pre-select,
// юзер сразу попадает в нужный flow.
type PaymentMethodChoice =
  | 'auto'
  | 'bank_card'
  | 'sbp'
  | 'tinkoff_bank'
  | 'sberbank';

const PAYMENT_METHODS: { value: PaymentMethodChoice; label: string; hint: string }[] = [
  { value: 'auto', label: 'Любой', hint: 'Выбрать на странице ЮKassa' },
  { value: 'bank_card', label: 'Карта', hint: 'Visa / MC / Мир' },
  { value: 'sbp', label: 'СБП', hint: 'По QR через приложение банка' },
  { value: 'tinkoff_bank', label: 'T-Pay', hint: 'Для клиентов Т-Банка' },
  { value: 'sberbank', label: 'SberPay', hint: 'Для клиентов Сбербанка' },
];

const schema = z.object({
  amount: z
    .number({ invalid_type_error: 'Введи число' })
    .min(MIN_TOPUP_RUB, `Минимум ${MIN_TOPUP_RUB} ₽ за пополнение`)
    .max(1_000_000, 'Максимум 1 000 000 ₽ за раз'),
  payment_method: z.enum([
    'auto',
    'bank_card',
    'sbp',
    'tinkoff_bank',
    'sberbank',
  ]),
});
type FormValues = z.infer<typeof schema>;

export function TopupCard() {
  const topup = useTopup();
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { amount: 1_000, payment_method: 'auto' },
  });
  const selectedMethod = watch('payment_method');

  async function onSubmit(values: FormValues) {
    try {
      const result = await topup.mutateAsync({
        amount_rub: values.amount,
        return_url: typeof window !== 'undefined' ? window.location.href : '/app/billing',
        // 'auto' = no pre-selection → ЮKassa shows its own picker.
        ...(values.payment_method !== 'auto' && {
          payment_method: values.payment_method,
        }),
      });
      // Реальный flow: редирект на confirmation_url ЮKassa.
      // В моках handler возвращает relative URL — открываем его в той же вкладке.
      window.location.assign(result.confirmation_url);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'validation_error') {
        setError('amount', { message: err.message });
        return;
      }
      const msg =
        err instanceof ApiClientError ? err.message : 'Не удалось создать платёж. Попробуй ещё раз.';
      toast.error(msg);
    }
  }

  return (
    <Card>
      <CardDescription>Пополнить баланс</CardDescription>
      <CardTitle>Через ЮKassa</CardTitle>

      <form noValidate onSubmit={handleSubmit(onSubmit)} className="mt-4 flex flex-col gap-4">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Быстрые суммы">
          {PRESETS.map((p) => (
            <Button
              key={p}
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setValue('amount', p, { shouldValidate: true })}
            >
              {p.toLocaleString('ru-RU')} ₽
            </Button>
          ))}
        </div>

        <Field>
          <Label htmlFor="topup-amount">Своя сумма</Label>
          <Input
            id="topup-amount"
            type="number"
            min={MIN_TOPUP_RUB}
            step={100}
            inputMode="numeric"
            invalid={Boolean(errors.amount)}
            aria-describedby="topup-amount-help topup-amount-error"
            {...register('amount', { valueAsNumber: true })}
          />
          <FieldHelper id="topup-amount-help">
            Минимум {MIN_TOPUP_RUB} ₽. Платёж проходит через ЮKassa.
          </FieldHelper>
          <FieldError id="topup-amount-error">{errors.amount?.message}</FieldError>
        </Field>

        <Field>
          <Label>Способ оплаты</Label>
          <div
            role="radiogroup"
            aria-label="Способ оплаты"
            className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
          >
            {PAYMENT_METHODS.map((m) => {
              const active = selectedMethod === m.value;
              return (
                <button
                  key={m.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() =>
                    setValue('payment_method', m.value, { shouldValidate: true })
                  }
                  className={[
                    'flex flex-col items-start rounded-md border px-3 py-2 text-left text-sm',
                    'transition focus:outline-none focus:ring-2 focus:ring-offset-1',
                    active
                      ? 'border-primary bg-primary/5 ring-1 ring-primary'
                      : 'border-border hover:border-primary/40',
                  ].join(' ')}
                  data-testid={`topup-method-${m.value}`}
                >
                  <span className="font-medium">{m.label}</span>
                  <span className="text-xs text-muted-foreground">{m.hint}</span>
                </button>
              );
            })}
          </div>
          <FieldHelper id="topup-method-help">
            «Любой» — ЮKassa покажет все варианты на своей странице.
          </FieldHelper>
        </Field>

        <Button
          type="submit"
          loading={isSubmitting || topup.isPending}
          className="w-full"
          data-testid="topup-submit"
        >
          Перейти к оплате
        </Button>
      </form>
    </Card>
  );
}
