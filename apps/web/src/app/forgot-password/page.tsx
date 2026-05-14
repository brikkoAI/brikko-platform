'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { AuthShell } from '@/components/auth/AuthShell';
import { authApi, ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

const schema = z.object({
  email: z.string().min(1, 'Введи email').email('Проверь формат email'),
});

type FormValues = z.infer<typeof schema>;

export default function ForgotPasswordPage() {
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    try {
      await authApi.forgotPassword(values.email.trim().toLowerCase());
      setSubmittedEmail(values.email);
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось отправить письмо.';
      toast.error(message);
    }
  }

  if (submittedEmail) {
    return (
      <AuthShell
        title="Ссылка отправлена"
        subtitle={`Если аккаунт с email ${submittedEmail} существует — отправили ссылку для сброса пароля. Ссылка действует 1 час.`}
      >
        <p
          style={{
            fontSize: 14,
            color: 'var(--fg-muted)',
            textAlign: 'center',
          }}
        >
          <Link href="/login" className="brikko-link">
            ← Вернуться ко входу
          </Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Сбросить пароль"
      subtitle="Укажи email — отправим ссылку для установки нового пароля."
    >
      <form noValidate onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-5">
        <div className="brikko-field-stack">
          <label htmlFor="forgot-email" className="brikko-field-label">
            Email
          </label>
          <input
            id="forgot-email"
            type="email"
            autoComplete="email"
            inputMode="email"
            aria-invalid={errors.email ? true : undefined}
            aria-describedby="forgot-email-error"
            className="brikko-input"
            {...register('email')}
          />
          <p
            id="forgot-email-error"
            role={errors.email ? 'alert' : undefined}
            className="brikko-field-error"
          >
            {errors.email?.message ?? ' '}
          </p>
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          aria-busy={isSubmitting || undefined}
          aria-disabled={isSubmitting || undefined}
          className="brikko-cta-primary"
          style={{ width: '100%', justifyContent: 'center' }}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>Отправляем</span>
            </>
          ) : (
            <span>Отправить ссылку</span>
          )}
        </button>

        <p style={{ fontSize: 14, color: 'var(--fg-muted)', textAlign: 'center' }}>
          <Link href="/login" className="brikko-link">
            Вспомнил пароль — войти
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
