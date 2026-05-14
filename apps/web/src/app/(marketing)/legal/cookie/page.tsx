export const metadata = {
  title: 'Cookie-политика',
  description: 'Какие cookie использует Brikko и как ими управлять.',
};

/**
 * Заглушка cookie-политики. Финальная версия — после внедрения cookie-banner'а.
 */
export default function CookieStubPage() {
  return (
    <section className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold text-fg-primary">Cookie-политика</h1>
      <p className="mt-4 text-body text-fg-muted">
        Brikko использует строго необходимые cookie: сессия пользователя
        (HttpOnly), CSRF-токен и preference языка. Аналитических cookie нет.
      </p>
      <p className="mt-4 text-body text-fg-muted">
        Полный текст политики будет опубликован до публичного запуска.
      </p>
    </section>
  );
}
