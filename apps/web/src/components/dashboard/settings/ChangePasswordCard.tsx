'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Field, FieldError, FieldHelper } from '@/components/ui/form';
import { useChangePasswordV2 } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

/**
 * Смена пароля внутри Settings.
 *
 * UX-обоснование:
 *  - Inline-форма в Card — не модалка. Внутри Settings уже есть поток «изменить настройки»,
 *    модалка добавила бы лишнее переключение контекста.
 *  - Validation: минимум 8, 1 цифра, 1 буква (не «12+ символов») — это **practical baseline**
 *    с CEO-decisions.md. Жесткие требования (special chars, no-words-from-dictionary)
 *    создают frustration без security-выгоды для prepaid B2B SaaS.
 *  - Поле `confirm` отдельное — стандартный pattern против опечаток.
 */

const schema = z
  .object({
    current_password: z.string().min(1, 'Текущий пароль обязателен'),
    new_password: z
      .string()
      .min(8, 'Минимум 8 символов')
      .refine((v) => /\d/.test(v), 'Должна быть хотя бы одна цифра')
      .refine((v) => /[a-zA-Zа-яА-Я]/.test(v), 'Должна быть хотя бы одна буква'),
    confirm: z.string(),
  })
  .refine((d) => d.new_password === d.confirm, {
    path: ['confirm'],
    message: 'Пароли не совпадают',
  })
  .refine((d) => d.new_password !== d.current_password, {
    path: ['new_password'],
    message: 'Новый пароль не должен совпадать с текущим',
  });

type FormValues = z.infer<typeof schema>;

export function ChangePasswordCard() {
  const change = useChangePasswordV2();
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    try {
      await change.mutateAsync({
        current_password: values.current_password,
        new_password: values.new_password,
      });
      toast.success('Пароль изменён.');
      reset();
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'invalid_credentials') {
        setError('current_password', { message: 'Текущий пароль не совпадает' });
        return;
      }
      if (err instanceof ApiClientError && err.type === 'validation_error') {
        setError('new_password', { message: err.message });
        return;
      }
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось обновить пароль.';
      toast.error(message);
    }
  }

  return (
    <Card>
      <CardTitle>Сменить пароль</CardTitle>
      <CardDescription className="mt-1">
        После смены: текущая сессия останется, но другие сессии можно завершить выше.
      </CardDescription>

      <form
        noValidate
        onSubmit={handleSubmit(onSubmit)}
        className="mt-4 flex flex-col gap-4"
        data-testid="change-password-form"
      >
        <Field>
          <Label htmlFor="current-password">Текущий пароль</Label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            invalid={Boolean(errors.current_password)}
            aria-describedby="current-password-error"
            {...register('current_password')}
          />
          <FieldError id="current-password-error">{errors.current_password?.message}</FieldError>
        </Field>

        <Field>
          <Label htmlFor="new-password-v2">Новый пароль</Label>
          <Input
            id="new-password-v2"
            type="password"
            autoComplete="new-password"
            invalid={Boolean(errors.new_password)}
            aria-describedby="new-password-help-v2 new-password-error-v2"
            {...register('new_password')}
          />
          <FieldHelper id="new-password-help-v2">
            Минимум 8 символов, 1 цифра, 1 буква.
          </FieldHelper>
          <FieldError id="new-password-error-v2">{errors.new_password?.message}</FieldError>
        </Field>

        <Field>
          <Label htmlFor="confirm-password-v2">Повтори новый</Label>
          <Input
            id="confirm-password-v2"
            type="password"
            autoComplete="new-password"
            invalid={Boolean(errors.confirm)}
            aria-describedby="confirm-password-error-v2"
            {...register('confirm')}
          />
          <FieldError id="confirm-password-error-v2">{errors.confirm?.message}</FieldError>
        </Field>

        <Button type="submit" loading={isSubmitting || change.isPending} className="self-start">
          Сменить пароль
        </Button>
      </form>
    </Card>
  );
}
