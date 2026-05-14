import Link from 'next/link';
import type { Route } from 'next';
import { ArrowRight } from 'lucide-react';
import { getAllRecipes } from '@/lib/cookbook';

export const metadata = {
  title: 'Cookbook — готовые рецепты под бизнес-задачи · Brikko',
  description:
    'Пять production-ready рецептов под задачи юристов, sales, support, edtech и SaaS: код на Python, расчёт стоимости, выбор модели через Smart Router. Для api.brikko.ru.',
  alternates: { canonical: '/docs/cookbook' },
} as const;

const AUDIENCE_LABEL: Record<string, string> = {
  legal: 'Юристы',
  'hr-tech': 'HR-tech / Sales',
  edtech: 'EdTech',
  saas: 'SaaS / B2B',
  b2b: 'B2B',
};

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const slice = text.slice(0, max);
  const lastSpace = slice.lastIndexOf(' ');
  return (lastSpace > max * 0.6 ? slice.slice(0, lastSpace) : slice).trimEnd() + '…';
}

function formatCost(rub: number): string {
  if (rub < 1) {
    return `${rub.toFixed(2).replace('.', ',')} ₽`;
  }
  return `${Math.round(rub).toLocaleString('ru-RU')} ₽`;
}

export default async function CookbookIndexPage() {
  const recipes = await getAllRecipes();

  return (
    <section className="mx-auto max-w-6xl px-6 py-16 lg:py-24">
      <header className="max-w-3xl">
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Cookbook
        </p>
        <h1 className="brikko-h1 mt-3">Готовые рецепты под бизнес-задачи</h1>
        <p className="brikko-lede mt-4">
          Реальные сценарии использования Brikko: юр-документы с PII-маскингом,
          классификация лидов, проверка эссе, follow-up email. На каждый рецепт —
          выбор модели с обоснованием, готовый код, расчёт стоимости на 1000
          запросов.
        </p>
      </header>

      <ul className="mt-12 grid gap-5 md:grid-cols-2">
        {recipes.map((r) => (
          <li key={r.slug} className="h-full">
            <Link
              href={`/docs/cookbook/${r.slug}` as Route}
              className="brikko-card-link group h-full"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="brikko-pill">
                  {AUDIENCE_LABEL[r.audience] ?? r.audience}
                </span>
                <span className="font-mono text-body-sm text-fg-faint">
                  ~{formatCost(r.estimated_cost_per_1000_calls)} / 1000 запросов
                </span>
              </div>

              <h2 className="mt-4 text-lg font-semibold text-fg-primary">
                {r.title}
              </h2>
              <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                {truncate(r.description, 120)}
              </p>

              <div className="mt-4 flex flex-wrap gap-1.5">
                {r.tags.slice(0, 4).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-md border border-[var(--hairline)] bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-xs text-fg-muted"
                  >
                    {tag}
                  </span>
                ))}
              </div>

              <span className="mt-5 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                Открыть рецепт
                <ArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </span>
            </Link>
          </li>
        ))}
      </ul>

      <p className="mt-10 max-w-3xl text-body-sm text-fg-muted">
        Стоимость рассчитана на типовом размере запроса (см. секцию «Калькулятор» в
        каждом рецепте) по нашему публичному прайсу в рублях. Welcome-бонуса 200 ₽
        хватит на 200–16 000 запросов в зависимости от модели.
      </p>
    </section>
  );
}
