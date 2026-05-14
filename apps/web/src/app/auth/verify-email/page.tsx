'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { AlertTriangle, Mail, Loader2 } from 'lucide-react';
import { z } from 'zod';
import { AuthShell } from '@/components/auth/AuthShell';
import { useResendEmailVerification } from '@/lib/auth';
import { ApiClientError } from '@/lib/api';
import { toast } from '@/components/ui/toast';

/**
 * /auth/verify-email — handles `?error=expired` (срок ссылки прошёл) и общий
 * entry-point для повторной отправки.
 *
 * UX-обоснование:
 *  - Отдельный маршрут от /signup/verify-email потому что: (1) этот сценарий
 *    приходит из email-callback'а от старой ссылки (другой entry), (2) у нас
 *    нет token'а в URL, (3) пользователь, возможно, уже залогинен (другая
 *    вкладка) — UI не должен путать «первый раз верифицируешь» с «повтор после
 *    expiry».
 *  - Если в URL есть `?email=` — pre-fill это поле и сразу разрешаем resend без
 *    явного ввода. Backend security: anti-enumeration (всегда 200), pre-fill
 *    безопасен.
 *  - 5-минутный cooldown — защита от bot-spam'а. countdown в секундах в кнопке.
 */

const RESEND_COOLDOWN_MS = 5 * 60 * 1000;

const emailSchema = z.string().min(1, 'Введи email').email('Проверь формат email');

function VerifyEmailContent() {
  const params = useSearchParams();
  const error = params.get('error');
  const isExpired = error === 'expired';
  const initialEmail = params.get('email') ?? '';

  const [email, setEmail] = useState(initialEmail);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const resend = useResendEmailVerification();

  useEffect(() => {
    if (!cooldownUntil) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [cooldownUntil]);

  const cooldownLeftMs =
    cooldownUntil && cooldownUntil > now ? cooldownUntil - now : 0;
  const onCooldown = cooldownLeftMs > 0;
  const cooldownLabel = formatCooldown(cooldownLeftMs);

  async function handleResend() {
    const result = emailSchema.safeParse(email.trim());
    if (!result.success) {
      setEmailError(result.error.errors[0]?.message ?? 'Проверь формат email');
      return;
    }
    setEmailError(null);
    try {
      await resend.mutateAsync(result.data.toLowerCase());
      toast.success('Письмо отправлено. Проверь почту.');
      setCooldownUntil(Date.now() + RESEND_COOLDOWN_MS);
    } catch (err) {
      const message =
        err instanceof ApiClientError ? err.message : 'Не удалось отправить письмо.';
      toast.error(message);
    }
  }

  return (
    <AuthShell
      title={isExpired ? 'Ссылка просрочена' : 'Подтверди email'}
      subtitle={
        isExpired
          ? 'Это нормально — ссылки живут 24 часа. Пришлём свежее письмо.'
          : 'Введи свой email и пришлём ссылку для подтверждения.'
      }
    >
      <div className="flex flex-col" style={{ gap: 24 }}>
        <div className="flex justify-center">
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 56,
              height: 56,
              borderRadius: '9999px',
              background: isExpired
                ? 'rgba(217, 119, 6, 0.10)'
                : 'var(--bg-elevated)',
              color: isExpired ? '#d97706' : 'var(--fg-primary)',
              border: '1px solid var(--hairline)',
            }}
            aria-hidden="true"
          >
            {isExpired ? (
              <AlertTriangle className="h-7 w-7" strokeWidth={1.25} />
            ) : (
              <Mail className="h-7 w-7" strokeWidth={1.25} />
            )}
          </div>
        </div>

        <div className="brikko-field-stack">
          <label htmlFor="verify-email" className="brikko-field-label">
            Email
          </label>
          <input
            id="verify-email"
            type="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={emailError ? true : undefined}
            aria-describedby="verify-email-help verify-email-error"
            className="brikko-input"
            data-testid="verify-email-input"
          />
          <p id="verify-email-help" className="brikko-field-helper">
            На него пришлём свежую ссылку.
          </p>
          <p
            id="verify-email-error"
            role={emailError ? 'alert' : undefined}
            className="brikko-field-error"
          >
            {emailError ?? ' '}
          </p>
        </div>

        <button
          type="button"
          onClick={handleResend}
          disabled={resend.isPending || onCooldown}
          aria-busy={resend.isPending || undefined}
          aria-disabled={resend.isPending || onCooldown || undefined}
          className="brikko-cta-primary"
          style={{ width: '100%', justifyContent: 'center' }}
          data-testid="resend-verification-cta"
        >
          {resend.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.5} aria-hidden="true" />
              <span>Отправляем</span>
            </>
          ) : onCooldown ? (
            <span>Повторно через {cooldownLabel}</span>
          ) : (
            <span>Прислать новое письмо</span>
          )}
        </button>

        <Link
          href="/login"
          className="brikko-cta-secondary"
          style={{ width: '100%', justifyContent: 'center' }}
        >
          Войти
        </Link>
      </div>
    </AuthShell>
  );
}

function formatCooldown(ms: number): string {
  const total = Math.ceil(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function VerifyEmailExpiredPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailContent />
    </Suspense>
  );
}
