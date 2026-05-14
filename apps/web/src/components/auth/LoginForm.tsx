'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { useLogin } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

export const loginSchema = z.object({
  email: z.string().min(1, 'Введи email').email('Проверь формат email'),
  password: z.string().min(1, 'Введи пароль'),
});

export type LoginFormValues = z.infer<typeof loginSchema>;

/**
 * LoginForm — cream-style (Sprint 13.5). Сохраняет контракт ?reason=...:
 *   - `session_required` / `session_expired` — info-banner
 *   - `password_reset_success` — success-banner
 *   - неизвестные значения silently ignored
 *
 * Форма опирается на utility-классы из brikko-marketing.css (`brikko-input`,
 * `brikko-cta-primary`, `brikko-link`, `brikko-banner-*`). Тесты используют
 * semantic queries (label/role) + data-testid — стилевой переход их не ломает.
 */
type LoginReason =
  | 'session_required'
  | 'session_expired'
  | 'password_reset_success'
  | 'oauth_cancelled'
  | 'oauth_invalid_state'
  | 'oauth_provider_error'
  | 'oauth_email_required'
  | 'oauth_email_taken'
  | 'oauth_user_missing'
  | 'account_inactive';

type ReasonConfig = {
  variant: 'info' | 'success' | 'error';
  title: string;
  description: string;
};

const REASON_BANNERS: Record<LoginReason, ReasonConfig> = {
  session_required: {
    variant: 'info',
    title: 'Войди в аккаунт',
    description: 'Чтобы открыть личный кабинет, нужно войти.',
  },
  session_expired: {
    variant: 'info',
    title: 'Сессия истекла',
    description: 'Войди заново — твой аккаунт и данные на месте.',
  },
  password_reset_success: {
    variant: 'success',
    title: 'Пароль обновлён',
    description: 'Войди с новым паролем.',
  },
  oauth_cancelled: {
    variant: 'info',
    title: 'Вход отменён',
    description: 'Ты не подтвердил доступ у провайдера. Можно попробовать ещё раз.',
  },
  oauth_invalid_state: {
    variant: 'error',
    title: 'Не удалось подтвердить вход',
    description: 'Срок действия ссылки истёк или она пришла из чужого окна. Начни заново.',
  },
  oauth_provider_error: {
    variant: 'error',
    title: 'Провайдер не ответил',
    description: 'Google или Yandex временно не отвечают. Попробуй email-вход или повтори через минуту.',
  },
  oauth_email_required: {
    variant: 'error',
    title: 'Нужен доступ к email',
    description: 'Выдай провайдеру разрешение на email — без него мы не сможем создать аккаунт.',
  },
  oauth_email_taken: {
    variant: 'info',
    title: 'Email уже зарегистрирован',
    description: 'Войди паролем и привяжи провайдер в настройках безопасности.',
  },
  oauth_user_missing: {
    variant: 'error',
    title: 'Аккаунт не найден',
    description: 'Учётка к этой OAuth-привязке удалена. Зарегистрируйся заново.',
  },
  account_inactive: {
    variant: 'error',
    title: 'Аккаунт неактивен',
    description: 'Свяжись с поддержкой support@brikko.ru.',
  },
};

function isLoginReason(value: string | null): value is LoginReason {
  return value !== null && value in REASON_BANNERS;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const rawReason = params.get('reason');
  const banner = isLoginReason(rawReason) ? REASON_BANNERS[rawReason] : null;
  const emailHint = params.get('email')?.trim().toLowerCase() ?? '';

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: emailHint, password: '' },
  });

  async function onSubmit(values: LoginFormValues) {
    try {
      await login.mutateAsync({
        email: values.email.trim().toLowerCase(),
        password: values.password,
      });
      router.push('/app');
    } catch (err) {
      if (err instanceof ApiClientError && err.type === 'invalid_credentials') {
        setError('password', { message: 'Неверный email или пароль' });
        return;
      }
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось войти. Попробуй ещё раз.';
      toast.error(message);
    }
  }

  const submitting = isSubmitting || login.isPending;

  return (
    <form
      noValidate
      onSubmit={handleSubmit(onSubmit)}
      className="flex flex-col gap-5"
      data-testid="login-form"
    >
      {banner ? (
        <div
          role="status"
          className={`brikko-banner brikko-banner-${banner.variant}`}
          data-testid="login-reason-banner"
        >
          <div className="flex flex-1 flex-col">
            <p className="brikko-banner-title">{banner.title}</p>
            <p className="brikko-banner-desc">{banner.description}</p>
          </div>
        </div>
      ) : null}

      <OAuthButtons />

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
        <label htmlFor="login-email" className="brikko-field-label">
          Email
        </label>
        <input
          id="login-email"
          type="email"
          autoComplete="email"
          inputMode="email"
          aria-describedby="login-email-error"
          aria-invalid={errors.email ? true : undefined}
          className="brikko-input"
          {...register('email')}
        />
        <p
          id="login-email-error"
          role={errors.email ? 'alert' : undefined}
          className="brikko-field-error"
        >
          {errors.email?.message ?? ' '}
        </p>
      </div>

      <div className="brikko-field-stack">
        <div className="flex items-center justify-between">
          <label htmlFor="login-password" className="brikko-field-label">
            Пароль
          </label>
          <Link href="/forgot-password" className="brikko-link" style={{ fontSize: 13 }}>
            Забыл?
          </Link>
        </div>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          aria-describedby="login-password-error"
          aria-invalid={errors.password ? true : undefined}
          className="brikko-input"
          {...register('password')}
        />
        <p
          id="login-password-error"
          role={errors.password ? 'alert' : undefined}
          className="brikko-field-error"
        >
          {errors.password?.message ?? ' '}
        </p>
      </div>

      <button
        type="submit"
        disabled={submitting}
        aria-busy={submitting || undefined}
        aria-disabled={submitting || undefined}
        className="brikko-cta-primary"
        style={{ width: '100%', justifyContent: 'center' }}
        data-testid="login-submit"
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            <span>Входим</span>
          </>
        ) : (
          <span>Войти</span>
        )}
      </button>

      <p className="text-center" style={{ fontSize: 14, color: 'var(--fg-muted)' }}>
        Нет аккаунта?{' '}
        <Link href="/signup" className="brikko-link">
          Зарегистрироваться
        </Link>
      </p>
    </form>
  );
}

/**
 * OAuth-кнопки на /login. Бэкенд `/v1/auth/oauth/{provider}/start` редиректит
 * пользователя к Google / Yandex; после consent — `/callback` ставит cookie-сессию
 * и 302-ит обратно на `next` (по умолчанию `/app`).
 *
 * Сами кнопки — обычные `<a>`, потому что весь флоу — server-side redirects.
 * Никакого fetch/PKCE на клиенте: мы — confidential client, secret хранится в
 * gateway, а PKCE для social login не требуется (стандарт RFC 6749 §4.1).
 */
function OAuthButtons() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <OAuthButton provider="google" />
      <OAuthButton provider="yandex" />
    </div>
  );
}

interface OAuthButtonProps {
  provider: 'google' | 'yandex';
}

function OAuthButton({ provider }: OAuthButtonProps) {
  // NEXT_PUBLIC_API_BASE_URL обычно уже содержит `/v1` (например
  // `https://api.brikko.ru/v1`). Срезаем его, иначе получим
  // `/v1/v1/auth/oauth/...` → 404.
  const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? '').replace(/\/v1\/?$/, '');
  const startUrl = `${apiBase}/v1/auth/oauth/${provider}/start`;
  const label = provider === 'google' ? 'Войти через Google' : 'Войти через Яндекс';

  return (
    <a
      href={startUrl}
      data-testid={`oauth-${provider}-button`}
      className="brikko-btn brikko-btn-secondary"
      style={{
        width: '100%',
        justifyContent: 'center',
        textDecoration: 'none',
      }}
    >
      <ProviderIcon provider={provider} />
      <span>{label}</span>
    </a>
  );
}

function ProviderIcon({ provider }: OAuthButtonProps) {
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
