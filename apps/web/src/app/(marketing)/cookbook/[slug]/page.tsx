import Link from 'next/link';
import type { Route } from 'next';
import { notFound } from 'next/navigation';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { RECIPES, getRecipe } from '../recipes';

interface PageProps {
  // Next.js 15+ — params приходит как Promise. Распаковываем через await.
  params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
  return RECIPES.map((r) => ({ slug: r.slug }));
}

export async function generateMetadata({ params }: PageProps) {
  const { slug } = await params;
  const recipe = getRecipe(slug);
  if (!recipe) return { title: 'Cookbook · Brikko' };
  return {
    title: `${recipe.shortTitle} — JSON Schema cookbook · Brikko`,
    description: recipe.metaDescription,
  };
}

/**
 * Cookbook recipe page (legacy /cookbook/[slug], Cream Studio v6).
 * Sections: header → JSON Schema → System prompt → input → output → curl → CTA.
 */
export default async function RecipePage({ params }: PageProps) {
  const { slug } = await params;
  const recipe = getRecipe(slug);
  if (!recipe) notFound();

  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <Link
        href={'/cookbook' as Route}
        className="inline-flex items-center gap-1 text-body-sm font-medium text-fg-muted transition-colors hover:text-fg-primary"
      >
        <ArrowLeft className="h-4 w-4" strokeWidth={1.5} />
        Все рецепты
      </Link>

      <h1 className="mt-6 text-balance text-3xl font-semibold tracking-tight text-fg-primary sm:text-4xl">
        {recipe.title}
      </h1>
      <p className="brikko-lede mt-4">{recipe.intro}</p>
      <p className="mt-3 text-body text-fg-muted">{recipe.description}</p>

      <Section title="JSON Schema">
        <CodeBlock code={recipe.jsonSchema} language="json" />
      </Section>

      <Section title="System prompt">
        <CodeBlock code={recipe.systemPrompt} language="text" />
      </Section>

      <Section title="Пример input">
        <blockquote className="rounded-r-2xl border-l-2 border-[var(--fg-primary)] bg-[var(--bg-elevated)] p-4 text-body italic text-fg-muted">
          {recipe.exampleInput}
        </blockquote>
      </Section>

      <Section title="Пример output">
        <CodeBlock code={recipe.exampleOutput} language="json" />
      </Section>

      <Section title="curl">
        <CodeBlock code={recipe.curlSnippet} language="bash" />
      </Section>

      {recipe.notes ? (
        <aside className="brikko-card-flat mt-8 text-body-sm text-fg-muted">
          <strong className="font-semibold text-fg-primary">Примечание.</strong>{' '}
          {recipe.notes}
        </aside>
      ) : null}

      <div className="brikko-cta-card mt-10">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200 ₽ в баланс, карта не нужна. Endpoint{' '}
          <code className="brikko-code-inline">https://api.brikko.ru/v1</code>{' '}
          совместим с OpenAI SDK из коробки.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Попробовать в кабинете</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.5} />
        </Link>
      </div>
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="text-xl font-semibold text-fg-primary">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <div className="brikko-code-shell">
      <pre className="brikko-code-pre" data-language={language}>
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}
