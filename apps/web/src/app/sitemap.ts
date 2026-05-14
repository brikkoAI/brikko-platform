import type { MetadataRoute } from 'next';
import { getRecipeSlugs } from '@/lib/cookbook';
import { getIntegrationSlugs } from '@/lib/integrations';
import { BRAND } from '@/lib/brand';

/**
 * Sitemap — Sprint 11 (2026-05-01).
 *
 * Next.js 14 App Router идиома: экспортируем default async function, Next
 * автоматически рендерит /sitemap.xml на билде. Никаких дополнительных
 * конфигов next-sitemap не нужно — для нашего размера (≈15 URL) это
 * избыточная зависимость.
 *
 * Что включаем:
 *   - публичные маршруты лендинга (главная, /pricing, /models, /faq, /status)
 *   - юр-страницы (оферта, реквизиты, политики)
 *   - документация: /docs, /docs/cookbook, /docs/smart-routing
 *   - 5 cookbook-рецептов (динамически через getRecipeSlugs)
 *
 * Что НЕ включаем:
 *   - /login /signup /forgot-password /reset-password — auth-страницы, не для индекса
 *   - /app/* — за auth, не индексируется
 *   - /api/* — нет смысла
 *   - /(старый) /cookbook — JSON Schema recipes; оставляем неиндексируемым,
 *     основной cookbook теперь /docs/cookbook (Sprint 11 решение).
 */

const SITE = `https://${BRAND.domain}`;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();

  const staticEntries: MetadataRoute.Sitemap = [
    { url: `${SITE}/`, lastModified: now, changeFrequency: 'weekly', priority: 1.0 },
    { url: `${SITE}/pricing`, lastModified: now, changeFrequency: 'weekly', priority: 0.9 },
    { url: `${SITE}/models`, lastModified: now, changeFrequency: 'weekly', priority: 0.9 },
    { url: `${SITE}/faq`, lastModified: now, changeFrequency: 'monthly', priority: 0.6 },
    { url: `${SITE}/status`, lastModified: now, changeFrequency: 'daily', priority: 0.5 },
    { url: `${SITE}/integrations`, lastModified: now, changeFrequency: 'weekly', priority: 0.8 },
    // MCP distribution-лендинг (S4, 2026-05-12). Входная точка для разработчиков,
    // ищущих «MCP server для Brikko» / «Claude Cursor MCP gateway».
    { url: `${SITE}/mcp`, lastModified: now, changeFrequency: 'weekly', priority: 0.8 },
    { url: `${SITE}/docs`, lastModified: now, changeFrequency: 'weekly', priority: 0.7 },
    { url: `${SITE}/docs/cookbook`, lastModified: now, changeFrequency: 'weekly', priority: 0.8 },
    { url: `${SITE}/docs/smart-routing`, lastModified: now, changeFrequency: 'monthly', priority: 0.8 },
    // Sprint 14 (2026-05-06) — настоящие /docs страницы. Заглушки (api/messages,
    // api/embeddings и т.д.) НЕ включаем в sitemap — Google пенализирует
    // «coming soon» / тонкий контент.
    { url: `${SITE}/docs/getting-started`, lastModified: now, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE}/docs/api/chat-completions`, lastModified: now, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE}/docs/api/anonymize`, lastModified: now, changeFrequency: 'monthly', priority: 0.8 },
    { url: `${SITE}/docs/cli`, lastModified: now, changeFrequency: 'monthly', priority: 0.7 },
    { url: `${SITE}/docs/concepts/privacy-v2`, lastModified: now, changeFrequency: 'monthly', priority: 0.7 },
    { url: `${SITE}/legal/info`, lastModified: now, changeFrequency: 'monthly', priority: 0.3 },
    { url: `${SITE}/legal/oferta`, lastModified: now, changeFrequency: 'monthly', priority: 0.3 },
    { url: `${SITE}/legal/privacy`, lastModified: now, changeFrequency: 'monthly', priority: 0.3 },
    { url: `${SITE}/legal/cookie`, lastModified: now, changeFrequency: 'yearly', priority: 0.2 },
    // Lead-magnet landing для compliance / CTO. Sprint 14 (2026-05-07).
    // Высокий priority — это входная точка для compliance-segmenta поиска
    // («152-ФЗ ChatGPT», «GPT персональные данные»).
    { url: `${SITE}/legal/152-fz`, lastModified: now, changeFrequency: 'monthly', priority: 0.7 },
  ];

  const recipeSlugs = await getRecipeSlugs();
  const recipeEntries: MetadataRoute.Sitemap = recipeSlugs.map((slug) => ({
    url: `${SITE}/docs/cookbook/${slug}`,
    lastModified: now,
    changeFrequency: 'monthly',
    priority: 0.7,
  }));

  const integrationEntries: MetadataRoute.Sitemap = getIntegrationSlugs().map((slug) => ({
    url: `${SITE}/integrations/${slug}`,
    lastModified: now,
    changeFrequency: 'monthly',
    priority: 0.7,
  }));

  return [...staticEntries, ...recipeEntries, ...integrationEntries];
}
