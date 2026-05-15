import { Suspense } from 'react';
import { SignupForm } from '@/components/auth/SignupForm';
import { AuthShell } from '@/components/auth/AuthShell';

export const metadata = {
  title: 'Регистрация',
  description: 'Создай аккаунт за 30 секунд. 200 ₽ на тесты при регистрации.',
  // Auth-страница не для индекса (см. login/page.tsx).
  robots: { index: false, follow: false },
};

// Force dynamic rendering — SignupForm читает ?tier= через useSearchParams.
// Без этого Next.js пытается prerender'ить /signup статически и падает с
// "useSearchParams() should be wrapped in a suspense boundary" даже когда
// Suspense есть (см. https://nextjs.org/docs/messages/missing-suspense-with-csr-bailout).
export const dynamic = 'force-dynamic';

export default function SignupPage() {
  return (
    <AuthShell
      title="Создать аккаунт"
      subtitle="200 ₽ welcome-бонуса сразу после подтверждения email и доступ ко всем 38 моделям. Карта не нужна."
    >
      {/* Suspense обязателен — SignupForm читает ?tier= через useSearchParams.
          Без него Next.js делает client-side bailout для всей страницы. */}
      <Suspense fallback={null}>
        <SignupForm />
      </Suspense>
    </AuthShell>
  );
}
