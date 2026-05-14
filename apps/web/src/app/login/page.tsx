import { Suspense } from 'react';
import { LoginForm } from '@/components/auth/LoginForm';
import { AuthShell } from '@/components/auth/AuthShell';

export const metadata = {
  title: 'Вход',
  description: 'Войди в личный кабинет Brikko.',
  // Auth-страница не должна попадать в индекс: для пользователя бесполезна без
  // сессии, а GSC ругался на /app → /login redirect-цепочки.
  robots: { index: false, follow: false },
};

/**
 * Page-level Suspense обязателен: LoginForm читает useSearchParams (?reason=session_expired),
 * и Next 14 требует Suspense boundary НА УРОВНЕ page.tsx, чтобы выполнить SSR-bailout
 * корректно. Внутренняя Suspense внутри LoginForm.tsx не помогает в SSR-режиме —
 * форма всё равно попадает в client-only branch без unique fallback'а.
 */
export default function LoginPage() {
  return (
    <AuthShell
      title="Войти"
      subtitle="Используй email и пароль, которые задавал при регистрации."
    >
      <Suspense fallback={<LoginFormSkeleton />}>
        <LoginForm />
      </Suspense>
    </AuthShell>
  );
}

function LoginFormSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-hidden="true">
      <div
        className="h-10 animate-pulse rounded-xl"
        style={{ background: 'var(--bg-elevated)' }}
      />
      <div
        className="h-10 animate-pulse rounded-xl"
        style={{ background: 'var(--bg-elevated)' }}
      />
      <div
        className="h-11 animate-pulse rounded-full"
        style={{ background: 'var(--bg-elevated)' }}
      />
    </div>
  );
}
