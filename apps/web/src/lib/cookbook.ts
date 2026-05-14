import { promises as fs } from 'node:fs';
import path from 'node:path';
import matter from 'gray-matter';
import { marked } from 'marked';

/**
 * Cookbook loader — Sprint 11 (2026-05-01).
 *
 * Читает 5 markdown-рецептов из `src/content/cookbook/*.md` (frontmatter +
 * body), парсит gray-matter и рендерит тело в HTML через marked. Всё
 * происходит во время билда (server component → SSG), поэтому fs.readdir
 * допустим: на runtime в браузере этот модуль никогда не выполняется.
 *
 * Дизайн-решения:
 *   - Никакого MDX: контент чисто-статичный, JSX в .md не нужен. marked
 *     даёт ~30 КБ на серверный bundle, MDX-стек тащит >300 КБ.
 *   - Внутренние ссылки в .md написаны как `/cookbook/...` (исторический
 *     префикс). Перед рендером нормализуем к `/docs/cookbook/...` —
 *     единая точка правды на маршрут страницы.
 *   - Тело отдаём как `body_html` (готовый HTML), компонент вставит через
 *     dangerouslySetInnerHTML внутри <article className="prose">. Это
 *     безопасно: source — наши собственные .md из репозитория (trusted).
 */

const CONTENT_DIR = path.join(process.cwd(), 'src', 'content', 'cookbook');

export type RecipeAudience =
  | 'legal'
  | 'hr-tech'
  | 'edtech'
  | 'saas'
  | 'b2b'
  | string;

export interface Recipe {
  slug: string;
  title: string;
  description: string;
  model_recommended: string;
  tags: readonly string[];
  audience: RecipeAudience;
  estimated_cost_per_1000_calls: number;
  created_at: string;
  body_html: string;
}

interface RecipeFrontmatter {
  title?: unknown;
  slug?: unknown;
  description?: unknown;
  model_recommended?: unknown;
  tags?: unknown;
  audience?: unknown;
  estimated_cost_per_1000_calls?: unknown;
  created_at?: unknown;
}

function asString(value: unknown, field: string, file: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`cookbook: '${field}' must be a non-empty string in ${file}`);
  }
  return value;
}

function asNumber(value: unknown, field: string, file: string): number {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    throw new Error(`cookbook: '${field}' must be a number in ${file}`);
  }
  return value;
}

function asStringArray(value: unknown, field: string, file: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`cookbook: '${field}' must be an array in ${file}`);
  }
  return value.map((v, i) => {
    if (typeof v !== 'string') {
      throw new Error(`cookbook: '${field}[${i}]' must be a string in ${file}`);
    }
    return v;
  });
}

function asDateString(value: unknown, field: string, file: string): string {
  // gray-matter parses YAML dates → JS Date by default. Handle both.
  if (value instanceof Date) {
    const iso = value.toISOString().slice(0, 10);
    return iso;
  }
  if (typeof value === 'string' && value.length > 0) {
    return value;
  }
  throw new Error(`cookbook: '${field}' must be a date or ISO string in ${file}`);
}

function normalizeInternalLinks(html: string): string {
  // Markdown-source uses /cookbook/<slug>; страница живёт на /docs/cookbook/<slug>.
  // Переписываем только href'ы (никаких других /cookbook-вхождений в HTML быть не
  // должно), без regex по всему документу — целевой обстрел href-атрибута.
  return html.replace(/href="\/cookbook\//g, 'href="/docs/cookbook/');
}

async function loadRecipeFile(slug: string): Promise<Recipe> {
  const filePath = path.join(CONTENT_DIR, `${slug}.md`);
  const raw = await fs.readFile(filePath, 'utf8');
  const parsed = matter(raw);
  const fm = parsed.data as RecipeFrontmatter;
  const fileLabel = `${slug}.md`;

  const title = asString(fm.title, 'title', fileLabel);
  const description = asString(fm.description, 'description', fileLabel);
  const modelRecommended = asString(
    fm.model_recommended,
    'model_recommended',
    fileLabel,
  );
  const audience = asString(fm.audience, 'audience', fileLabel);
  const tags = asStringArray(fm.tags, 'tags', fileLabel);
  const estimatedCost = asNumber(
    fm.estimated_cost_per_1000_calls,
    'estimated_cost_per_1000_calls',
    fileLabel,
  );
  const createdAt = asDateString(fm.created_at, 'created_at', fileLabel);

  // marked v12+ возвращает Promise<string> у async-варианта; используем sync,
  // чтобы вызов остался простым (нет async-extensions у нас).
  const rawHtml = marked.parse(parsed.content, { async: false }) as string;
  const bodyHtml = normalizeInternalLinks(rawHtml);

  return {
    slug,
    title,
    description,
    model_recommended: modelRecommended,
    tags,
    audience,
    estimated_cost_per_1000_calls: estimatedCost,
    created_at: createdAt,
    body_html: bodyHtml,
  };
}

let cache: Recipe[] | null = null;

async function loadAll(): Promise<Recipe[]> {
  if (cache) return cache;
  const files = await fs.readdir(CONTENT_DIR);
  const slugs = files
    .filter((f) => f.endsWith('.md'))
    .map((f) => f.replace(/\.md$/, ''));
  const recipes = await Promise.all(slugs.map((s) => loadRecipeFile(s)));
  // Сортировка: created_at desc (новое сверху), затем slug ASC для детерминизма.
  recipes.sort((a, b) => {
    if (a.created_at !== b.created_at) {
      return a.created_at < b.created_at ? 1 : -1;
    }
    return a.slug.localeCompare(b.slug);
  });
  cache = recipes;
  return recipes;
}

export async function getAllRecipes(): Promise<Recipe[]> {
  return loadAll();
}

export async function getRecipe(slug: string): Promise<Recipe | null> {
  const all = await loadAll();
  return all.find((r) => r.slug === slug) ?? null;
}

export async function getRecipeSlugs(): Promise<string[]> {
  const all = await loadAll();
  return all.map((r) => r.slug);
}

/**
 * Подбор связанных рецептов по пересечению тегов.
 * Возвращает максимум `limit` записей (default 2), отсортированных по
 * количеству общих тегов desc, при равенстве — по created_at desc.
 */
export async function getRelatedRecipes(
  slug: string,
  limit: number = 2,
): Promise<Recipe[]> {
  const all = await loadAll();
  const current = all.find((r) => r.slug === slug);
  if (!current) return [];
  const currentTags = new Set(current.tags);
  const scored = all
    .filter((r) => r.slug !== slug)
    .map((r) => ({
      recipe: r,
      score: r.tags.filter((t) => currentTags.has(t)).length,
    }))
    .filter((x) => x.score > 0)
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return a.recipe.created_at < b.recipe.created_at ? 1 : -1;
    })
    .slice(0, limit)
    .map((x) => x.recipe);
  return scored;
}
