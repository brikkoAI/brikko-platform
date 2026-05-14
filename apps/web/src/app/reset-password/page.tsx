'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { AuthShell } from '@/components/auth/AuthShell';
import { authApi, ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

const schema = z
  .object({
    password: z.string().min(10, 'Минимум 10 символов'),
    confirm: z.string(),
  })
  .refine((d) => d.password === d.confirm, {
    path: ['confirm'],
    message: 'Пароли не совпадают',
  });

type FormValues = z.infer<typeof schema>;

function ResetPasswordContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  // Sprint 6: email можно прокинуть в /reset-password?email=... — мы пробросим
  // его в /login?email=... для preFill. Это **не** замена auto-login: пользователь
  // должен явно ввести новый пароль (NIST 800-63B recommended pattern).
  const emailHint = searchParams.get('email');

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  if (!token) {
    return (
      <AuthShell
        title="Ссылка недействительна"
        subtitle="В ссылке нет токена. Запроси сброс пароля заново."
      >
        <Link
          href="/forgot-password"
          className="brikko-cta-primary"
          style={{ width: '100%', justifyContent: 'center' }}
        >
          Запросить новую ссылку
        </Link>
      </AuthShell>
    );
  }

  // Token гарантированно есть после early-return. Сохраняем в const, чтобы TS
  // narrowed-тип `string` сохранился внутри закрытия onSubmit без `!` non-null.
  const validToken: string = token;

  async function onSubmit(values: FormValues) {
    try {
      await authApi.resetPassword(validToken, values.password);
      // Toast показываем сразу — при перезагрузке /login (после server-side redirect)
      // ничего не теряется. Stripe/Linear pattern.
      toast.success('Пароль успешно изменён.');
      const target = emailHint
        ? `/login?reason=password_reset_success&email=${encodeURIComponent(emailHint)}`
        : '/login?reason=password_reset_success';
      router.push(target as never);
    } catch (err) {
      const message =
        err instanceof ApiClientError && err.type === 'token_expired'
          ? 'Ссылка устарела. Запроси новую.'
          : err instanceof ApiClientError
            ? err.message
            : 'Не удалось обновить пароль.';
      toast.error(message);
    }
  }

  const submitting = isSubmitting;

  return (
    <AuthShell
      title="Новый пароль"
      subtitle="Придумай новый пароль — минимум 10 символов."
    >
      <form noValidate onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-5">
        <div className="brikko-field-stack">
          <label htmlFor="new-password" className="brikko-field-label">
            Новый пароль
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            aria-invalid={errors.password ? true : undefined}
            aria-describedby="new-password-help new-password-error"
            className="brikko-input"
            {...register('password')}
          />
          <p id="new-password-help" className="brikko-field-helper">
            Минимум 10 символов.
          </p>
          <p
            id="new-password-error"
            role={errors.password ? 'alert' : undefined}
            className="brikko-field-error"
          >
            {errors.password?.message ?? ' '}
          </p>
        </div>

        <div className="brikko-field-stack">
          <label htmlFor="confirm-password" className="brikko-field-label">
            Повтори пароль
          </label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            aria-invalid={errors.confirm ? true : undefined}
            aria-describedby="confirm-password-error"
            className="brikko-input"
            {...register('confirm')}
          />
          <p
            id="confirm-password-error"
            role={errors.confirm ? 'alert' : undefined}
            className="brikko-field-error"
          >
            {errors.confirm?.message ?? ' '}
          </p>
        </div>

        <button
          type="submit"
          disabled={submitting}
          aria-busy={submitting || undefined}
          aria-disabled={submitting || undefined}
          className="brikko-cta-primary"
          style={{ width: '100%', justifyContent: 'center' }}
        >
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>Сохраняем</span>
            </>
          ) : (
            <span>Сохранить новый пароль</span>
          )}
        </button>
      </form>
    </AuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordContent />
    </Suspense>
  );
}
