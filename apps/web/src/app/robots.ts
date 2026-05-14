import type { MetadataRoute } from 'next';
import { BRAND } from '@/lib/brand';

/**
 * robots.txt — Next.js 14 App Router идиома (default export → /robots.txt).
 *
 * Что разрешаем:
 *   - публичный лендинг + /docs/* + /legal/* + /pricing + /models + /integrations + /mcp
 *
 * Что запрещаем:
 *   - /api/*       — JSON-эндпоинты, не для индекса
 *   - /app/*       — за auth (личный кабинет)
 *   - /login /signup /forgot-password /reset-password — auth-формы
 *   - /monitoring/* — Sentry tunnel route (см. next.config.mjs)
 *   - /demo /preview /playground — служебные, не SEO-материал
 *
 * Sitemap пишем явно — Googlebot подхватывает быстрее, чем по дефолту.
 */
export default function robots(): MetadataRoute.Robots {
  const site = `https://${BRAND.domain}`;
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: [
          '/api/',
          '/app/',
          '/login',
          '/signup',
          '/forgot-password',
          '/reset-password',
          '/monitoring/',
          '/demo',
          '/preview',
          '/playground',
        ],
      },
    ],
    sitemap: `${site}/sitemap.xml`,
    host: site,
  };
}
