import Link from 'next/link';
import type { Route } from 'next';
import { ArrowRight } from 'lucide-react';
import { RECIPES } from './recipes';

export const metadata = {
  title: 'Cookbook · Brikko',
  description:
    'Готовые JSON Schema рецепты для частых задач: квалификация лидов, обогащение CRM, парсинг договоров, классификация support, извлечение медданных. Для GPT-5.5, Claude, Gemini через api.brikko.ru.',
};

/**
 * Cookbook index — список 5 готовых рецептов.
 *
 * Sprint 9 (2026-05-01): эта страница появилась как SEO-контент под ключевые слова
 * «JSON Schema GPT», «strict mode response_format», «классификация лидов AI» и т.д.
 * Каждый рецепт — отдельная подстраница /cookbook/[slug].
 */
export default function CookbookIndexPage() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-16 lg:py-24">
      <p className="brikko-eyebrow">
        <span className="brikko-eyebrow-dot" aria-hidden="true" />
        Cookbook
      </p>
      <h1 className="brikko-h1 mt-3">
        Cookbook — готовые JSON Schema рецепты
      </h1>
      <p className="brikko-lede mt-4">
        Пять production-ready схем под частые B2B-задачи: классификация лидов,
        обогащение CRM, парсинг договоров, диспетчеризация support и извлечение
        медданных. Каждый рецепт — system-prompt + JSON Schema + curl-сниппет под
        OpenAI-совместимый endpoint{' '}
        <code className="brikko-code-inline">api.brikko.ru/v1</code>.
      </p>

      <ul className="mt-10 grid gap-4 md:grid-cols-2">
        {RECIPES.map((r) => (
          <li key={r.slug}>
            <Link
              href={`/cookbook/${r.slug}` as Route}
              className="brikko-card-link group h-full"
            >
              <h2 className="text-lg font-semibold text-fg-primary">
                {r.shortTitle}
              </h2>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">{r.intro}</p>
              <span className="mt-4 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                Открыть рецепт{' '}
                <ArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.5}
                />
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <p className="mt-10 text-body-sm text-fg-faint">
        Все рецепты используют формат{' '}
        <code className="brikko-code-inline">response_format: json_schema</code>{' '}
        со <code className="brikko-code-inline">strict: true</code>. Это
        гарантирует, что ответ модели валидируется по схеме на стороне провайдера
        — без дополнительной пост-обработки и парсинга на твоей стороне.
      </p>
    </section>
  );
}
