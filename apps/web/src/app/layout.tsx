import type { Metadata } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { Source_Serif_4 } from 'next/font/google';
import { BRAND } from '@/lib/brand';
import { WebVitalsReporter } from '@/components/WebVitalsReporter';
import { Providers } from './providers';
import './globals.css';

// Sprint 13.7 (Cream Studio v6 polish, 2026-05-05): Inter и JetBrains Mono
// заменены на Geist Sans / Geist Mono. Inter был в banned-list бренда —
// единый Vercel-stack теперь покрывает body + mono без второй загрузки.
// next/font/google для Geist не используем — пакет `geist` отдает шрифты
// локально (NextJS обертка), без runtime-зависимости от Google Fonts.

// Sprint 13 (Cream Studio v6 rebrand): Source Serif 4 для editorial-typography
// на marketing-страницах. Используется через `font-family: 'Source Serif 4'`
// напрямую в .brikko-h1 / .brikko-h2 / .brikko-menu-link / footer brand.
const sourceSerif = Source_Serif_4({
  subsets: ['latin', 'cyrillic-ext'],
  weight: ['400', '500'],
  style: ['normal', 'italic'],
  variable: '--font-serif',
  display: 'swap',
});

// PII pivot (2026-05-14, BRIEF_v2_pivot.md): главная теперь продаёт AI Privacy
// Ecosystem, а не gateway-каталог моделей. Title/description/keywords/OG/Twitter
// обновлены под маскинг ПД перед ChatGPT/Claude/Gemini.
// TODO: replace og-image.png under PII pivot — designer task.
// Сейчас OG-картинка генерится edge-функцией `app/opengraph-image.tsx` (старый
// дизайн с tagline gateway). Дизайнер заменит на статический PNG в /public.
export const metadata: Metadata = {
  title: {
    default: `${BRAND.name} — AI без утечки 152-ФЗ. Маскируем ПД перед ChatGPT, Claude, Gemini.`,
    template: `%s · ${BRAND.name}`,
  },
  description:
    'Открытая инфраструктура маскинга персональных данных перед AI. ФИО, ИНН, СНИЛС, паспорт РФ — локально через Natasha NER. 6 способов установить за минуту: расширение, desktop, CLI, skill, n8n, Python.',
  keywords: [
    '152-ФЗ',
    'AI маскинг',
    'PII',
    'ChatGPT приватность',
    'Brikko',
    'Natasha NER',
    'presidio',
    'ИНН маскирование',
    'обезличивание ПД',
    'n8n privacy',
    'Claude Code skill',
  ],
  metadataBase: new URL(`https://${BRAND.domain}`),
  // НЕ задаём корневой `alternates.canonical` — Next 14 не имеет токена
  // "self-canonical" и любое значение здесь распространяется на ВСЕ дочерние
  // страницы (например `canonical: '/'` сделает /pricing указывать на /).
  // Канонизация хостов покрыта Caddy 301-редиректами (www/.online/.tech/etc.
  // → brikko.ru). Если нужно явное canonical для конкретной страницы —
  // задавай `alternates.canonical: '/path'` в её собственной metadata.
  openGraph: {
    title: `${BRAND.name} — AI Privacy Ecosystem для русского рынка`,
    description:
      'Маскируем ФИО, ИНН, СНИЛС, паспорт РФ перед отправкой в ChatGPT, Claude, Gemini. 6 способов установить.',
    url: `https://${BRAND.domain}`,
    siteName: BRAND.name,
    locale: 'ru_RU',
    type: 'website',
    // images НЕ задаём вручную — Next.js автоматически подхватывает
    // `app/opengraph-image.tsx` (edge ImageResponse, 1200×630). Когда дизайнер
    // положит статический /public/og-image.png — добавить сюда явно с alt.
  },
  twitter: {
    card: 'summary_large_image',
    title: `${BRAND.name} — AI без утечки 152-ФЗ`,
    description:
      'Маскируем ПД перед AI. 6 способов установить: расширение, desktop, CLI, skill, n8n, Python.',
    // images — то же что и для OG, Next.js подхватит `app/twitter-image.tsx`
    // или fallback на opengraph-image.
  },
  // Static favicon.ico — Google Search Console читает именно этот файл,
  // динамический /icon (Next.js generated) Google не всегда подхватывает.
  // Порядок важен: первый — то, что Google использует в SERP-сниппете.
  icons: {
    icon: [
      { url: '/favicon.ico', sizes: 'any' },
      { url: '/icon', type: 'image/png', sizes: '32x32' },
    ],
    apple: '/apple-icon',
  },
  manifest: '/manifest.json',
};

/**
 * FOUC-prevention: устанавливаем data-theme на <html> до first paint, чтобы
 * marketing/auth/dashboard рендерились в правильной теме без вспышки светлой.
 * Источник истины — useTheme hook (`brikko-theme` ключ, fallback на
 * prefers-color-scheme: dark). Скрипт продублирован inline т.к. React-state
 * hydrate'ится только после JS bundle, к этому моменту первый paint уже был.
 */
const themeFOUCScript = `
(function() {
  try {
    var stored = window.localStorage.getItem('brikko-theme');
    var theme = (stored === 'light' || stored === 'dark')
      ? stored
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="ru"
      className={`${GeistSans.variable} ${GeistMono.variable} ${sourceSerif.variable}`}
    >
      <head>
        {/* eslint-disable-next-line react/no-danger */}
        <script dangerouslySetInnerHTML={{ __html: themeFOUCScript }} />
      </head>
      <body>
        <WebVitalsReporter />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
