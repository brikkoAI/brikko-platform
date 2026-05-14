'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { BRAND } from '@/lib/brand';

/**
 * Брендированный fallback при `throw` внутри RSC/Client component.
 *
 * Используется в:
 *   - app/error.tsx (любая страница в /app/* — кроме /app/error.tsx)
 *   - app/(marketing)/error.tsx (если будет добавлен)
 *   - app/global-error.tsx (как fallback для самого root layout'а)
 *
 * Sentry.captureException вызывается в parent error.tsx (а не здесь), чтобы
 * этот компонент оставался простым «отрисовщиком» без побочных эффектов.
 */
export interface ErrorPageProps {
  /** Sentry digest, если ошибка пойймана server-side (Next.js пробрасывает). */
  digest?: string;
  /** Reset-handler от Next error boundary — обновляет maybe-recoverable error. */
  onRetry?: () => void;
  /** Текст для кнопки «домой». Должен быть валидным Next.js route (typedRoutes). */
  homeHref?: Route;
  homeLabel?: string;
}

export function ErrorPage({
  digest,
  onRetry,
  homeHref,
  homeLabel = 'На главную',
}: ErrorPageProps) {
  const home: Route = homeHref ?? ('/' as Route);
  return (
    <main
      role="main"
      className="mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center gap-6 px-6 py-16 text-center"
    >
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-error-50 text-error-600">
        <AlertTriangle className="h-7 w-7" strokeWidth={1.5} aria-hidden="true" />
      </div>

      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Что-то пошло не так</h1>
        <p className="mt-2 text-body text-gray-600">
          Мы уже знаем об ошибке и разбираемся. Попробуй обновить страницу. Если
          проблема не уходит — напиши нам, поможем.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3">
        {onRetry ? <Button onClick={onRetry}>Попробовать снова</Button> : null}
        <Button asChild variant="secondary">
          <Link href={home}>{homeLabel}</Link>
        </Button>
        <Button asChild variant="ghost">
          <a href={`mailto:${BRAND.supportEmail}`}>Написать в поддержку</a>
        </Button>
      </div>

      {digest ? (
        <p className="font-mono text-xs text-gray-400">id ошибки: {digest}</p>
      ) : null}
    </main>
  );
}
