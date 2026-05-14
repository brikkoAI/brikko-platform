import { SignupForm } from '@/components/auth/SignupForm';
import { AuthShell } from '@/components/auth/AuthShell';

export const metadata = {
  title: 'Регистрация',
  description: 'Создай аккаунт за 30 секунд. 200 ₽ на тесты при регистрации.',
  // Auth-страница не для индекса (см. login/page.tsx).
  robots: { index: false, follow: false },
};

export default function SignupPage() {
  return (
    <AuthShell
      title="Создать аккаунт"
      subtitle="200 ₽ welcome-бонуса сразу после подтверждения email и доступ ко всем 38 моделям. Карта не нужна."
    >
      <SignupForm />
    </AuthShell>
  );
}
