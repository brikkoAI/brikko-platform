// Voltari web Next.js config.
// Используем .mjs т.к. Next.js 14 не поддерживает .ts конфиг (только Next 15+).
// `output: 'standalone'` нужен для multi-stage Dockerfile (apps/web/Dockerfile)
// — тогда .next/standalone содержит минимальный set файлов для production runtime.

import { withSentryConfig } from '@sentry/nextjs';

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: 'standalone',
  // typedRoutes: ВКЛЮЧЕНО (закрывает TD-004). Все href типизированы — опечатка в
  // /app/billings или ссылка на несуществующий маршрут блокирует сборку.
  // Внешние URL (mailto:, https://) рендерим обычным <a>, не <Link>.
  experimental: {
    typedRoutes: true,
  },
  // TS strict ВКЛЮЧЁН: ignoreBuildErrors=true вырезан (TD-003 closed).
  // ESLint ВКЛЮЧЁН в build pipeline.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ];
  },
  // 301 www → apex: GSC ругался на "Duplicate without user-selected canonical",
  // потому что www.brikko.ru отдавал 200 рядом с brikko.ru. Делаем перманентный
  // редирект на apex — Google склеивает страницы и индексирует одну.
  async redirects() {
    return [
      {
        source: '/:path*',
        has: [{ type: 'host', value: 'www.brikko.ru' }],
        destination: 'https://brikko.ru/:path*',
        permanent: true,
      },
    ];
  },
};

// Sentry build-time wrapper. Безопасен при пустом DSN: если NEXT_PUBLIC_SENTRY_DSN
// не задан или содержит плейсхолдер, runtime-init no-op'ит (см. sentry.client.config.ts).
// Build-time опции `silent: true` глушат лишний вывод в CI, `widenClientFileUpload: true`
// загружает source maps для всего client-кода (нужно для production deminification).
export default withSentryConfig(config, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
  widenClientFileUpload: true,
  // Не загружаем source maps если SENTRY_AUTH_TOKEN не задан — позволяет билдить
  // без аккаунта Sentry.
  dryRun: !process.env.SENTRY_AUTH_TOKEN,
  tunnelRoute: '/monitoring',
  hideSourceMaps: true,
});
