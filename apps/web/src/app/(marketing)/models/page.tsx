import Link from 'next/link';
import { Search, ShieldCheck } from 'lucide-react';
import { EmptyState } from '@/components/ui/empty-state';
import { BRAND } from '@/lib/brand';
import {
  FALLBACK_MODELS,
  type PublicModel,
  type Provider,
  type Tier,
} from '@/lib/models-fallback';
import { ModelCard } from './ModelCard';
import { ModelsFilters, type CapabilityKey } from './ModelsFilters';

/**
 * /models — публичный каталог моделей. Cream Studio v6 (Sprint 13, 2026-05-02).
 *
 * Архитектура (без изменений в Sprint 13):
 *   - SSR fetch'им `https://api.brikko.ru/v1/models/public` с revalidate: 3600.
 *     Fallback на статичный FALLBACK_MODELS если API упал. Страница ВСЕГДА
 *     отдаёт 200 + контент.
 *   - Server component. Hydration платим только за тонкий ModelsFilters.
 *   - Фильтры через searchParams (multi-value): provider, tier, capability.
 *
 * Visual (Sprint 13 rebrand): индиго заменён на strict grayscale + theme-toggle.
 * H1, hero, картинки — в Cream Studio v6 эстетике. ModelCard пере-стилизован
 * через CSS-vars (см. ModelCard.tsx).
 */

const API_URL = `https://${BRAND.apiDomain}/v1/models/public`;

export const metadata = {
  title: 'Каталог LLM моделей — цены в ₽ за 1M токенов',
  description:
    'Полный каталог моделей Brikko: GPT-5.5, Claude Opus 4.7, Gemini 3.1 Pro, DeepSeek V4, YandexGPT, GigaChat. Прозрачные цены в рублях, контекстные окна, capabilities. Один API-ключ для всех.',
  alternates: { canonical: '/models' },
} as const;

export const revalidate = 3600;

interface PublicCatalogResponse {
  object: 'list';
  data: PublicModel[];
}

async function fetchModels(): Promise<PublicModel[]> {
  try {
    const res = await fetch(API_URL, {
      next: { revalidate: 3600 },
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = (await res.json()) as PublicCatalogResponse;
    if (!Array.isArray(json.data) || json.data.length === 0) {
      throw new Error('empty catalog');
    }
    return json.data;
  } catch {
    return [...FALLBACK_MODELS];
  }
}

// 9 провайдеров на момент 2026-05-11. Сегодня 6 активны через keyed-env
// (openai/anthropic/google/deepseek/yandex/sber), 3 готовы в backend но
// ждут UnionPay-аккаунта у CEO (moonshot/minimax/zhipu) — их count=0 в
// фильтре до момента появления ключа, FilterCheckbox автоматически
// рендерит disabled. Together (24 модели) скрыт endpoint'ом до момента
// когда CEO заведёт иностранную карту, не показываем в фильтрах вовсе.
const PROVIDER_OPTIONS: { value: Provider; label: string }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'yandex', label: 'Яндекс' },
  { value: 'sber', label: 'Сбер' },
  { value: 'moonshot', label: 'Moonshot (Kimi)' },
  { value: 'minimax', label: 'MiniMax (Hailuo)' },
  { value: 'zhipu', label: 'Zhipu (GLM)' },
];

const TIER_OPTIONS: { value: Tier; label: string }[] = [
  { value: 'NANO', label: 'Nano (самые дешёвые)' },
  { value: 'BUDGET', label: 'Budget' },
  { value: 'MID', label: 'Mid' },
  { value: 'FLAGSHIP', label: 'Flagship' },
  { value: 'PREMIUM', label: 'Premium' },
];

const CAPABILITY_OPTIONS: { value: CapabilityKey; label: string }[] = [
  { value: 'streaming', label: 'Streaming (SSE)' },
  { value: 'tool_calling', label: 'Tools (function calling)' },
  { value: 'json_schema_strict', label: 'Strict JSON Schema' },
  { value: 'vision', label: 'Vision (image input)' },
  { value: 'prompt_caching', label: 'Prompt caching' },
  { value: 'ru_legal', label: 'Размещено в РФ (152-ФЗ)' },
];

const TIER_ORDER: Record<Tier, number> = {
  PREMIUM: 0,
  FLAGSHIP: 1,
  MID: 2,
  BUDGET: 3,
  NANO: 4,
};

function asArray(value: string | string[] | undefined): string[] {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.length > 0) return [value];
  return [];
}

function applyFilters(
  models: readonly PublicModel[],
  filters: {
    providers: Set<string>;
    tiers: Set<string>;
    capabilities: Set<string>;
  },
): PublicModel[] {
  return models.filter((m) => {
    if (filters.providers.size > 0 && !filters.providers.has(m.provider)) return false;
    if (filters.tiers.size > 0 && !filters.tiers.has(m.tier)) return false;
    if (filters.capabilities.size > 0) {
      for (const cap of filters.capabilities) {
        if (!m.capabilities[cap as keyof PublicModel['capabilities']]) return false;
      }
    }
    return true;
  });
}

function sortForCatalog(models: PublicModel[]): PublicModel[] {
  return [...models].sort((a, b) => {
    const tierDiff = TIER_ORDER[a.tier] - TIER_ORDER[b.tier];
    if (tierDiff !== 0) return tierDiff;
    return a.id.localeCompare(b.id);
  });
}

function pickBest(models: readonly PublicModel[]): {
  classification: PublicModel | undefined;
  longContext: PublicModel | undefined;
  bestValue: PublicModel | undefined;
} {
  const byId = (id: string) => models.find((m) => m.id === id);

  const classification =
    byId('deepseek-v4-flash') ??
    models.find((m) => m.tier === 'NANO' && m.deprecated_at === null);

  const longContext =
    byId('gemini-3.1-pro') ??
    [...models]
      .filter((m) => m.deprecated_at === null)
      .sort((a, b) => b.context_tokens - a.context_tokens)[0];

  const bestValue =
    byId('deepseek-v4-flash') ??
    [...models]
      .filter((m) => m.deprecated_at === null && m.pricing_rub_per_1m.input !== null)
      .sort((a, b) => {
        const aSum =
          (a.pricing_rub_per_1m.input ?? Infinity) +
          (a.pricing_rub_per_1m.output ?? Infinity);
        const bSum =
          (b.pricing_rub_per_1m.input ?? Infinity) +
          (b.pricing_rub_per_1m.output ?? Infinity);
        return aSum - bSum;
      })[0];

  return { classification, longContext, bestValue };
}

interface ModelsPageProps {
  searchParams: Record<string, string | string[] | undefined>;
}

export default async function ModelsPage({ searchParams }: ModelsPageProps) {
  const allModels = await fetchModels();

  const filters = {
    providers: new Set(asArray(searchParams.provider)),
    tiers: new Set(asArray(searchParams.tier)),
    capabilities: new Set(asArray(searchParams.capability)),
  };

  const filtered = sortForCatalog(applyFilters(allModels, filters));
  const totalCount = allModels.length;
  const filteredCount = filtered.length;

  const providerCounts = countBy(allModels, (m) => m.provider);
  const tierCounts = countBy(allModels, (m) => m.tier);
  const capabilityCounts: Record<string, number> = {};
  for (const opt of CAPABILITY_OPTIONS) {
    capabilityCounts[opt.value] = allModels.filter(
      (m) => m.capabilities[opt.value as keyof PublicModel['capabilities']],
    ).length;
  }

  const best = pickBest(allModels);

  return (
    <>
      {/* Hero ----------------------------------------------------- */}
      <section
        style={{
          position: 'relative',
          padding: '180px 6vw 80px',
          maxWidth: 1400,
          margin: '0 auto',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Каталог
        </span>
        <h1
          className="brikko-h1"
          style={{ fontSize: 'clamp(40px, 5vw, 80px)', maxWidth: '14ch' }}
        >
          {totalCount} моделей. <span className="brikko-h2-italic">Один</span> API.
        </h1>
        <p className="brikko-lede" style={{ marginBottom: 24 }}>
          Прозрачные цены в рублях, единый Bearer-токен, чек после каждой оплаты.
          От самой дешёвой DeepSeek V4 Flash до GPT-5.5 Pro и Claude Opus 4.7.
        </p>
        <p
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 14px',
            border: '1px solid var(--hairline)',
            borderRadius: 9999,
            background: 'var(--accent-1-soft)',
            fontSize: 13,
            color: 'var(--fg-primary)',
            margin: '0 0 32px',
          }}
        >
          <ShieldCheck className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Все {totalCount} моделей поддерживают{' '}
          <strong style={{ fontWeight: 500 }}>Privacy v2</strong> — pre-LLM PII-маскинг.
        </p>
        <div>
          <Link href="/docs" className="brikko-btn brikko-btn-secondary">
            Использовать Smart Router
          </Link>
        </div>
      </section>

      <div className="brikko-divider" aria-hidden="true" />

      {/* Best picks ----------------------------------------------- */}
      <section
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '80px 6vw 0',
          position: 'relative',
          zIndex: 2,
        }}
        aria-labelledby="best-picks-heading"
      >
        <h2 id="best-picks-heading" className="sr-only">
          Рекомендации
        </h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: 16,
          }}
        >
          {best.classification ? (
            <ModelCard
              model={best.classification}
              variant="highlighted"
              highlightLabel="Лучшее для классификации"
            />
          ) : null}
          {best.longContext ? (
            <ModelCard
              model={best.longContext}
              variant="highlighted"
              highlightLabel="Лучшее для длинных контекстов"
            />
          ) : null}
          {best.bestValue ? (
            <ModelCard
              model={best.bestValue}
              variant="highlighted"
              highlightLabel="Лучшее за рубль"
            />
          ) : null}
        </div>
      </section>

      {/* Catalog: filters + grid ---------------------------------- */}
      <section
        aria-labelledby="catalog-heading"
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '64px 6vw 128px',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <h2 id="catalog-heading" className="sr-only">
          Полный каталог моделей
        </h2>
        <div style={{ display: 'grid', gap: 40, gridTemplateColumns: 'minmax(240px, 280px) 1fr' }}
          className="brikko-models-layout"
        >
          <ModelsFilters
            providers={PROVIDER_OPTIONS.map((opt) => ({
              ...opt,
              count: providerCounts[opt.value] ?? 0,
            }))}
            tiers={TIER_OPTIONS.map((opt) => ({
              ...opt,
              count: tierCounts[opt.value] ?? 0,
            }))}
            capabilities={CAPABILITY_OPTIONS.map((opt) => ({
              ...opt,
              count: capabilityCounts[opt.value] ?? 0,
            }))}
            totalCount={totalCount}
            filteredCount={filteredCount}
          />

          <div>
            {filtered.length === 0 ? (
              <EmptyState
                icon={<Search className="h-8 w-8" strokeWidth={1.5} />}
                title="По вашим фильтрам ничего не найдено"
                description="Сбросьте часть условий или выберите более широкий уровень модели."
              />
            ) : (
              <ul
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                  gap: 16,
                  listStyle: 'none',
                  padding: 0,
                  margin: 0,
                }}
                aria-label="Каталог моделей"
              >
                {filtered.map((m) => (
                  <li key={m.id} style={{ height: '100%' }}>
                    <ModelCard model={m} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <style>{`
          @media (max-width: 1023px) {
            .brikko-models-layout { grid-template-columns: 1fr !important; }
          }
        `}</style>
      </section>
    </>
  );
}

function countBy<T, K extends string>(
  list: readonly T[],
  key: (item: T) => K,
): Record<K, number> {
  const out = {} as Record<K, number>;
  for (const item of list) {
    const k = key(item);
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}
