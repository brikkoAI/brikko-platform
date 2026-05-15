'use client';

import { Mail, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react';
import Link from 'next/link';
import type { Route } from 'next';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef, useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { authApi, ApiClientError } from '@/lib/api';
import { useVerifyEmail } from '@/lib/auth';
import { toast } from '@/components/ui/toast';
import { formatKopecks } from '@/lib/utils';

/**
 * VerifyEmail flow content. Использует useSearchParams (?token=, ?email=) —
 * поэтому VerifyEmailPage ниже оборачивает компонент в Suspense на page-level.
 */
function VerifyEmailContent() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get('token');
  const email = params.get('email') ?? '';
  const verify = useVerifyEmail();

  // Идемпотентность через ref — устойчивость к React StrictMode double-mount.
  const triggered = useRef(false);
  const [resending, setResending] = useState(false);
  const [resentAt, setResentAt] = useState<Date | null>(null);
  // DEV-only verify URL — backend возвращает её в signup-response для DEV/staging
  // без SMTP. Production со SMTP такого поля не возвращает.
  const [devVerifyUrl, setDevVerifyUrl] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      setDevVerifyUrl(window.sessionStorage.getItem('brikko.dev_verify_url'));
    } catch {
      // privacy mode — кнопка не покажется.
    }
  }, []);

  useEffect(() => {
    if (!token || triggered.current) return;
    triggered.current = true;
    verify
      .mutateAsync(token)
      .then((res) => {
        toast.success(
          `Email подтверждён. На балансе ${formatKopecks(res.welcome_credit_kop, { forceFraction: true })} welcome-бонуса.`,
        );
        // Subscription pivot (CEO 2026-05-15): если signup пришёл с Pro/Team CTA,
        // ведём сразу в биллинг с открытым subscribe-flow вместо общего /app.
        // SignupForm стэшит tier в sessionStorage под TIER_STORAGE_KEY.
        //
        // Route-cast: typedRoutes ругается на динамический query-string, но это
        // на 100% safe — мы whitelist'им tier до 'pro' | 'team' выше.
        let dest: Route = '/app';
        try {
          const stashed = window.sessionStorage.getItem('brikko.signup_intent_tier');
          if (stashed === 'pro' || stashed === 'team') {
            dest = `/app/billing?action=subscribe&tier=${stashed}` as Route;
            window.sessionStorage.removeItem('brikko.signup_intent_tier');
          }
        } catch {
          // privacy mode — оставляем /app.
        }
        router.replace(dest);
      })
      .catch(() => {
        // ошибка отображается в state выше — не редиректим.
      });
  }, [token, router, verify]);

  async function handleResend() {
    if (!email.trim()) {
      toast.error('Введи email, на который хочешь повторно отправить ссылку.');
      return;
    }
    setResending(true);
    try {
      await authApi.resendVerification(email.trim().toLowerCase());
      setResentAt(new Date());
      toast.success('Отправили ссылку повторно. Проверь почту.');
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось отправить письмо.';
      toast.error(message);
    } finally {
      setResending(false);
    }
  }

  // Состояние 1: пользователь пришёл по ссылке c token, мы верифицируем.
  if (token) {
    if (verify.isPending) {
      return (
        <AuthShell title="Подтверждаем email" subtitle="Это займёт пару секунд.">
          <div className="flex justify-center" style={{ paddingTop: 8, paddingBottom: 16 }}>
            <Loader2
              className="h-10 w-10 animate-spin"
              strokeWidth={1.25}
              style={{ color: 'var(--fg-primary)' }}
              aria-hidden="true"
            />
          </div>
        </AuthShell>
      );
    }
    if (verify.isError) {
      const apiErr = verify.error as ApiClientError | undefined;
      const expired = apiErr instanceof ApiClientError && apiErr.type === 'token_expired';
      return (
        <AuthShell
          title={expired ? 'Ссылка истекла' : 'Ссылка повреждена'}
          subtitle={
            expired
              ? 'Запроси новую — отправим её повторно.'
              : 'Зарегистрируйся снова или войди.'
          }
        >
          <div className="flex flex-col items-center" style={{ gap: 24 }}>
            <AuthIconBubble variant="warn">
              <AlertTriangle className="h-7 w-7" strokeWidth={1.25} aria-hidden="true" />
            </AuthIconBubble>
            <div className="flex w-full flex-col" style={{ gap: 12 }}>
              <Link
                href="/signup"
                className="brikko-cta-primary"
                style={{ width: '100%', justifyContent: 'center' }}
              >
                Создать аккаунт
              </Link>
              <Link
                href="/login"
                className="brikko-cta-secondary"
                style={{ width: '100%', justifyContent: 'center' }}
              >
                Войти
              </Link>
            </div>
          </div>
        </AuthShell>
      );
    }
  }

  // Состояние 2: пользователь только что подал signup, ждёт письма.
  return (
    <AuthShell
      title="Проверь почту"
      subtitle={
        email
          ? `Отправили ссылку на ${email}. Ссылка действует 24 часа.`
          : 'Отправили ссылку для подтверждения email. Действует 24 часа.'
      }
    >
      <div className="flex flex-col items-center" style={{ gap: 20 }}>
        <AuthIconBubble variant="neutral">
          <Mail className="h-7 w-7" strokeWidth={1.25} aria-hidden="true" />
        </AuthIconBubble>

        <div
          className="w-full"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--hairline)',
            borderRadius: 12,
            padding: 16,
          }}
        >
          <p
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--fg-primary)',
              margin: 0,
            }}
          >
            Не пришло письмо?
          </p>
          <p
            style={{
              fontSize: 13,
              color: 'var(--fg-muted)',
              lineHeight: 1.55,
              margin: '4px 0 12px',
            }}
          >
            Проверь спам и папку «Промоакции». Если не нашёл — отправим ещё раз.
          </p>
          <button
            type="button"
            onClick={handleResend}
            disabled={resending}
            aria-busy={resending || undefined}
            aria-disabled={resending || undefined}
            className="brikko-cta-secondary"
            style={{ width: '100%', justifyContent: 'center' }}
            data-testid="resend-verification"
          >
            {resending ? (
              <>
                <Loader2
                  className="h-4 w-4 animate-spin"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
                <span>Отправляем</span>
              </>
            ) : (
              <span>Отправить ссылку ещё раз</span>
            )}
          </button>
          {resentAt ? (
            <p
              role="status"
              aria-live="polite"
              style={{
                marginTop: 10,
                fontSize: 12,
                color: 'var(--fg-muted)',
              }}
            >
              Отправлено в{' '}
              {resentAt.toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit',
              })}
              .
            </p>
          ) : null}
        </div>

        {devVerifyUrl ? (
          // DEV-only кнопка появляется только когда backend (DEV/staging без SMTP)
          // вернул реальный verify_url_dev. В production — отсутствует.
          <p style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
            <a
              href={devVerifyUrl}
              className="brikko-link"
              data-testid="dev-verify-link"
            >
              [DEV] Перейти по ссылке подтверждения
            </a>
          </p>
        ) : null}
      </div>
    </AuthShell>
  );
}

function AuthIconBubble({
  variant,
  children,
}: {
  variant: 'neutral' | 'warn';
  children: React.ReactNode;
}) {
  const bg =
    variant === 'warn'
      ? 'rgba(217, 119, 6, 0.10)'
      : 'var(--bg-elevated)';
  const fg = variant === 'warn' ? '#d97706' : 'var(--fg-primary)';
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 56,
        height: 56,
        borderRadius: '9999px',
        background: bg,
        color: fg,
        border: '1px solid var(--hairline)',
      }}
    >
      {children}
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <AuthShell title="Загружаем" subtitle="Минуту…">
          <div className="flex justify-center">
            <Loader2
              className="h-10 w-10 animate-spin"
              strokeWidth={1.25}
              style={{ color: 'var(--fg-primary)' }}
              aria-hidden="true"
            />
          </div>
        </AuthShell>
      }
    >
      <VerifyEmailContent />
    </Suspense>
  );
}
