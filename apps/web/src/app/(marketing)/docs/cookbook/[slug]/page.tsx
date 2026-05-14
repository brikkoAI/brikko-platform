import Link from 'next/link';
import type { Route } from 'next';
import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import {
  getRecipe,
  getRecipeSlugs,
  getRelatedRecipes,
  type Recipe,
} from '@/lib/cookbook';

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateStaticParams() {
  const slugs = await getRecipeSlugs();
  return slugs.map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const recipe = await getRecipe(slug);
  if (!recipe) return { title: 'Cookbook · Brikko' };
  return {
    title: `${recipe.title} · Cookbook · Brikko`,
    description: recipe.description,
    alternates: { canonical: `/docs/cookbook/${recipe.slug}` },
  };
}

const AUDIENCE_LABEL: Record<string, string> = {
  legal: 'Юристы',
  'hr-tech': 'HR-tech / Sales',
  edtech: 'EdTech',
  saas: 'SaaS / B2B',
  b2b: 'B2B',
};

function formatCost(rub: number): string {
  if (rub < 1) return `${rub.toFixed(2).replace('.', ',')} ₽`;
  return `${Math.round(rub).toLocaleString('ru-RU')} ₽`;
}

export default async function CookbookRecipePage({ params }: PageProps) {
  const { slug } = await params;
  const recipe = await getRecipe(slug);
  if (!recipe) notFound();

  const related = await getRelatedRecipes(recipe.slug, 2);

  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <Link
        href={'/docs/cookbook' as Route}
        className="inline-flex items-center gap-1 text-body-sm font-medium text-fg-muted transition-colors hover:text-fg-primary"
      >
        <ArrowLeft className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        Все рецепты
      </Link>

      <header className="mt-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="brikko-pill">
            {AUDIENCE_LABEL[recipe.audience] ?? recipe.audience}
          </span>
          <span className="font-mono text-body-sm text-fg-faint">
            ~{formatCost(recipe.estimated_cost_per_1000_calls)} / 1000 запросов
          </span>
          <span className="font-mono text-body-sm text-fg-faint">
            · модель: {recipe.model_recommended}
          </span>
        </div>
        <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight text-fg-primary sm:text-4xl">
          {recipe.title}
        </h1>
        <p className="brikko-lede mt-3">{recipe.description}</p>
      </header>

      <article
        className="recipe-prose mt-10"
        dangerouslySetInnerHTML={{ __html: recipe.body_html }}
      />

      {related.length > 0 ? (
        <aside
          aria-labelledby="related-heading"
          className="mt-16 border-t border-[var(--hairline)] pt-8"
        >
          <h2
            id="related-heading"
            className="text-body-sm font-medium uppercase tracking-wide text-fg-faint"
          >
            Связанные рецепты
          </h2>
          <ul className="mt-4 grid gap-4 md:grid-cols-2">
            {related.map((r) => (
              <li key={r.slug}>
                <RelatedCard recipe={r} />
              </li>
            ))}
          </ul>
        </aside>
      ) : null}

      <div className="brikko-cta-card mt-12">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс, карта не нужна. Endpoint{' '}
          <code className="brikko-code-inline">https://api.brikko.ru/v1</code>{' '}
          совместим с OpenAI SDK из коробки.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Попробовать в кабинете</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </div>
  );
}

function RelatedCard({ recipe }: { recipe: Recipe }) {
  return (
    <Link
      href={`/docs/cookbook/${recipe.slug}` as Route}
      className="brikko-card-link group h-full"
    >
      <span className="brikko-pill w-fit">
        {AUDIENCE_LABEL[recipe.audience] ?? recipe.audience}
      </span>
      <p className="mt-3 text-body font-semibold text-fg-primary">
        {recipe.title}
      </p>
    </Link>
  );
}
