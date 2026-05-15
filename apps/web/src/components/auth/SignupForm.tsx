'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { useSignup } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

/**
 * Tier query handling (CEO 2026-05-15, subscription pivot).
 *
 * Когда юзер пришёл с landing-pricing (/signup?tier=pro|team), мы:
 *   1. Стэшим выбранный tier в sessionStorage — переживёт click на verify-email
 *      ссылку из почты (новая вкладка) или back-forward.
 *   2. После успешной верификации email VerifyEmailPage прочитает stash и
 *      сделает redirect на /app/billing?action=subscribe&tier=<tier>
 *      вместо дефолтного /app.
 *
 * Whitelist tier-значений — защита от open-redirect-подобных манипуляций.
 */
const TIER_STORAGE_KEY = 'brikko.signup_intent_tier';
const ALLOWED_TIERS = ['pro', 'team'] as const;
type AllowedTier = (typeof ALLOWED_TIERS)[number];

function isAllowedTier(value: string | null): value is AllowedTier {
  return value !== null && (ALLOWED_TIERS as readonly string[]).includes(value);
}

export const signupSchema = z.object({
  email: z.string().min(1, 'Введи email').email('Проверь формат email'),
  password: z.string().min(10, 'Минимум 10 символов').max(128, 'Максимум 128 символов'),
  acceptTerms: z.boolean().refine((v) => v === true, {
    message: 'Нужно принять условия, чтобы продолжить',
  }),
});

export type SignupFormValues = z.infer<typeof signupSchema>;

/**
 * SignupForm — cream-style (Sprint 13.5). Логика onSubmit / DEV verify-link
 * sessionStorage / email_already_registered handling — без изменений.
 *
 * Visual: brikko-input + brikko-cta-primary + brikko-checkbox + brikko-link.
 * Тесты опираются на semantic queries и data-testid — стиль не ломает их.
 */
export function SignupForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const signup = useSignup();

  // Stash signup intent (Pro/Team) сразу при mount — даже если юзер уйдёт на
  // OAuth-redirect и вернётся, sessionStorage переживёт (same-tab).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const tier = searchParams.get('tier');
    try {
      if (isAllowedTier(tier)) {
        window.sessionStorage.setItem(TIER_STORAGE_KEY, tier);
      }
    } catch {
      // privacy mode — fallback на /app, юзер сам ткнёт «Оформить Pro» в дашборде.
    }
  }, [searchParams]);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<SignupFormValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: { email: '', password: '', acceptTerms: false },
  });

  async function onSubmit(values: SignupFormValues) {
    try {
      const res = await signup.mutateAsync({
        email: values.email.trim().toLowerCase(),
        password: values.password,
      });
      if (typeof window !== 'undefined') {
        try {
          if (res.verify_url_dev) {
            window.sessionStorage.setItem('brikko.dev_verify_url', res.verify_url_dev);
          } else {
            window.sessionStorage.removeItem('brikko.dev_verify_url');
          }
        } catch {
          // privacy mode — не critical.
        }
      }
      const params = new URLSearchParams({ email: res.email });
      router.push(`/signup/verify-email?${params.toString()}`);
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'email_already_registered') {
        setError('email', {
          message: 'Этот email уже в Brikko. Войди или восстанови пароль.',
        });
        return;
      }
      const message =
        err instanceof ApiClientError
          ? err.message
          : 'Не удалось зарегистрироваться. Попробуй ещё раз.';
      toast.error(message);
    }
  }

  const submitting = isSubmitting || signup.isPending;

  return (
    <form
      noValidate
      onSubmit={handleSubmit(onSubmit)}
      className="flex flex-col gap-5"
      data-testid="signup-form"
    >
      <SignupOAuthButtons />

      <div
        aria-hidden="true"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
          fontSize: 11,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--fg-faint)',
        }}
      >
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
        или email
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
      </div>

      <div className="brikko-field-stack">
        <label htmlFor="signup-email" className="brikko-field-label">
          Email
        </label>
        <input
          id="signup-email"
          type="email"
          autoComplete="email"
          inputMode="email"
          aria-describedby="signup-email-error"
          aria-invalid={errors.email ? true : undefined}
          className="brikko-input"
          {...register('email')}
        />
        <p
          id="signup-email-error"
          role={errors.email ? 'alert' : undefined}
          className="brikko-field-error"
        >
          {errors.email?.message ?? ' '}
        </p>
      </div>

      <div className="brikko-field-stack">
        <label htmlFor="signup-password" className="brikko-field-label">
          Пароль
        </label>
        <input
          id="signup-password"
          type="password"
          autoComplete="new-password"
          aria-describedby="signup-password-help signup-password-error"
          aria-invalid={errors.password ? true : undefined}
          className="brikko-input"
          {...register('password')}
        />
        <p id="signup-password-help" className="brikko-field-helper">
          Минимум 10 символов.
        </p>
        <p
          id="signup-password-error"
          role={errors.password ? 'alert' : undefined}
          className="brikko-field-error"
        >
          {errors.password?.message ?? ' '}
        </p>
      </div>

      <div className="brikko-field-stack">
        <label
          className="flex items-start gap-3"
          style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}
        >
          <input
            type="checkbox"
            className="brikko-checkbox"
            aria-describedby="signup-terms-error"
            {...register('acceptTerms')}
          />
          <span>
            Я принимаю{' '}
            <Link href="/legal/oferta" className="brikko-link">
              оферту
            </Link>{' '}
            и{' '}
            <Link href="/legal/privacy" className="brikko-link">
              политику конфиденциальности
            </Link>
            .
          </span>
        </label>
        <p
          id="signup-terms-error"
          role={errors.acceptTerms ? 'alert' : undefined}
          className="brikko-field-error"
        >
          {errors.acceptTerms?.message ?? ' '}
        </p>
      </div>

      <button
        type="submit"
        disabled={submitting}
        aria-busy={submitting || undefined}
        aria-disabled={submitting || undefined}
        className="brikko-cta-primary"
        style={{ width: '100%', justifyContent: 'center' }}
        data-testid="signup-submit"
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            <span>Создаём аккаунт</span>
          </>
        ) : (
          <span>Создать аккаунт</span>
        )}
      </button>

      <p className="text-center" style={{ fontSize: 14, color: 'var(--fg-muted)' }}>
        Уже есть аккаунт?{' '}
        <Link href="/login" className="brikko-link">
          Войти
        </Link>
      </p>
    </form>
  );
}

/**
 * OAuth-кнопки на /signup. Тот же эндпоинт что и на /login —
 * `/v1/auth/oauth/{provider}/start`. Бэкенд решает signup vs login
 * по наличию (provider, subject) в `oauth_identities`. Welcome-credit
 * 200 ₽ начисляется здесь же — single shared codepath.
 */
function SignupOAuthButtons() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <SignupOAuthButton provider="google" />
      <SignupOAuthButton provider="yandex" />
    </div>
  );
}

function SignupOAuthButton({ provider }: { provider: 'google' | 'yandex' }) {
  // strip trailing /v1 — иначе будет /v1/v1/auth/oauth/...
  const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? '').replace(/\/v1\/?$/, '');
  const startUrl = `${apiBase}/v1/auth/oauth/${provider}/start`;
  const label =
    provider === 'google' ? 'Зарегистрироваться через Google' : 'Зарегистрироваться через Яндекс';

  return (
    <a
      href={startUrl}
      data-testid={`oauth-${provider}-signup`}
      className="brikko-btn brikko-btn-secondary"
      style={{
        width: '100%',
        justifyContent: 'center',
        textDecoration: 'none',
      }}
    >
      <SignupProviderIcon provider={provider} />
      <span>{label}</span>
    </a>
  );
}

function SignupProviderIcon({ provider }: { provider: 'google' | 'yandex' }) {
  if (provider === 'google') {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
        <path
          fill="#4285F4"
          d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        />
        <path
          fill="#34A853"
          d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        />
        <path
          fill="#FBBC05"
          d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        />
        <path
          fill="#EA4335"
          d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="11" fill="#FC3F1D" />
      <path
        d="M13.7 6h-2.1c-2 0-3.5 1.3-3.5 3.2 0 1.5.7 2.5 2 3l-2.5 4.6h2l2.3-4.3h.6V17H14V6h-.3zm-.6 4.9h-.7c-.9 0-1.7-.5-1.7-1.7 0-1.3.8-1.7 1.6-1.7h.8v3.4z"
        fill="#fff"
      />
    </svg>
  );
}
