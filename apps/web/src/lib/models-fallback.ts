/**
 * Статичный fallback-каталог моделей для /models, когда api.brikko.ru
 * недоступен на момент SSR. Это **только safety-net**: production-источник
 * — `GET /v1/models/public` (см. apps/gateway/voltari_gateway/api/models.py).
 *
 * Контракт совпадает с `ModelSpec.to_public_dict()` из
 * `apps/gateway/voltari_gateway/router/catalog.py` — если backend поменяет
 * формат, сначала правим тип `PublicModel` ниже, потом этот файл.
 *
 * Цены в `pricing_rub_per_1m.input/output` — целое RUB/1M, посчитанное по
 * прайс-листу Brikko в рублях. Для fallback'а этого достаточно: пользователь
 * видит цены, даже если они на пару процентов разойдутся с актуальными —
 * лучше, чем пустая страница. Production-fetch перетирает их через
 * `revalidate: 3600`.
 *
 * NB: это не источник истины для биллинга — для биллинга работает router
 * по `kop_per_1k` (см. catalog.py). Здесь — только маркетинговая витрина.
 *
 * Provider-union включает 9 провайдеров — 6 активных сегодня
 * (openai/anthropic/google/deepseek/yandex/sber) + 3 готовых в backend, но
 * пока без ключа в env (moonshot/minimax/zhipu — нужен UnionPay, см.
 * apps/gateway/voltari_gateway/config.py §MOONSHOT_API_KEY). Когда ключи
 * появятся, /v1/models/public автоматически начнёт отдавать +12 моделей —
 * фронту переподготавливаться не нужно. Together (24 модели) сейчас тоже
 * фильтруется на endpoint'е (нет иностранной карты у CEO), включается тем же
 * способом.
 */

export type Provider =
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'deepseek'
  | 'yandex'
  | 'sber'
  | 'moonshot'
  | 'minimax'
  | 'zhipu'
  | 'together';

export type Tier = 'NANO' | 'BUDGET' | 'MID' | 'FLAGSHIP' | 'PREMIUM';

export interface PublicModel {
  id: string;
  display_name: string;
  provider: Provider;
  provider_display_name: string;
  category: string;
  tier: Tier;
  context_tokens: number;
  pricing_rub_per_1m: {
    input: number | null;
    output: number | null;
    cached_input: number | null;
    cache_write: number | null;
  };
  pricing_usd_per_1m_official: {
    input: number | null;
    output: number | null;
    cached_input: number | null;
  };
  modalities: {
    input: string[];
    output: string[];
  };
  capabilities: {
    streaming: boolean;
    tool_calling: boolean;
    json_schema_strict: boolean;
    vision: boolean;
    audio: boolean;
    prompt_caching: boolean;
    ru_legal: boolean;
  };
  deprecated_at: string | null;
  released_at: string | null;
  description: string | null;
  best_for: string[];
}

const PROVIDER_DISPLAY: Record<Provider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  deepseek: 'DeepSeek',
  yandex: 'Яндекс',
  sber: 'Сбер',
  moonshot: 'Moonshot (Kimi)',
  minimax: 'MiniMax (Hailuo)',
  zhipu: 'Zhipu (GLM)',
  together: 'Together AI',
};

/**
 * Хелпер: USD/1M → RUB/1M по прайс-листу Brikko.
 * Brikko публикует собственный открытый прайс в рублях за миллион токенов;
 * исходник вычисления — `to_public_dict()` в catalog.py. Здесь дублируем
 * формулу: USD_RUB(80) × коэффициент (1.15) с округлением half-up.
 * Округление до целого рубля — fallback не учитывает копейки, этого
 * достаточно для маркетинговой витрины.
 */
const rub = (usdPer1m: number): number => Math.round(usdPer1m * 80 * 1.15);

/**
 * Сжатый билдер: убирает шаблонный шум при описании ~40 строк каталога.
 * Все поля кроме явно переданных получают разумные дефолты.
 */
function model(args: {
  id: string;
  display_name: string;
  provider: Provider;
  tier: Tier;
  context_tokens: number;
  usd_input: number;
  usd_output: number;
  usd_cached?: number;
  capabilities?: Partial<PublicModel['capabilities']>;
  description?: string;
  deprecated_at?: string | null;
  best_for?: string[];
  vision?: boolean;
  ru_legal?: boolean;
  streaming?: boolean;
  tools?: boolean;
  strict_json?: boolean;
  prompt_caching?: boolean;
}): PublicModel {
  const cachedDiscounted =
    args.usd_cached !== undefined && args.usd_cached < args.usd_input;
  return {
    id: args.id,
    display_name: args.display_name,
    provider: args.provider,
    provider_display_name: PROVIDER_DISPLAY[args.provider],
    category: 'chat',
    tier: args.tier,
    context_tokens: args.context_tokens,
    pricing_rub_per_1m: {
      input: rub(args.usd_input),
      output: rub(args.usd_output),
      cached_input: cachedDiscounted ? rub(args.usd_cached as number) : null,
      cache_write: null,
    },
    pricing_usd_per_1m_official: {
      input: args.usd_input,
      output: args.usd_output,
      cached_input: cachedDiscounted ? (args.usd_cached as number) : null,
    },
    modalities: {
      input: args.vision ? ['text', 'image'] : ['text'],
      output: ['text'],
    },
    capabilities: {
      streaming: args.streaming ?? true,
      tool_calling: args.tools ?? true,
      json_schema_strict: args.strict_json ?? true,
      vision: args.vision ?? false,
      audio: false,
      prompt_caching:
        args.prompt_caching ??
        (cachedDiscounted ||
          ['anthropic', 'openai', 'deepseek'].includes(args.provider)),
      ru_legal: args.ru_legal ?? false,
      ...(args.capabilities ?? {}),
    },
    deprecated_at: args.deprecated_at ?? null,
    released_at: null,
    description: args.description ?? null,
    best_for: args.best_for ?? [],
  };
}

/**
 * 38 моделей front-party каталога — копия `CATALOG` из catalog.py
 * на момент 2026-05-11 (Sprint M3.1 + M3.2 expansion, Together скрыт
 * по фильтру endpoint'а). Порядок и содержимое сверены ручным diff'ом
 * по grep'у ``id="..."`` в catalog.py.
 *
 * Состав: OpenAI (15) + Anthropic (6) + Google (7) + DeepSeek (5) +
 * Yandex (2) + Sber (3) = 38. Provider'ы Moonshot/MiniMax/Zhipu и
 * Together добавлены в `Provider`-union (см. выше) но без записей в
 * fallback'е — `/v1/models/public` фильтрует их по env-ключам, не
 * нужно зеркалить в fallback пока CEO не оплатит UnionPay-аккаунт.
 */
export const FALLBACK_MODELS: readonly PublicModel[] = [
  // ---------- OpenAI (15) ----------
  model({
    id: 'gpt-5.4-mini',
    display_name: 'gpt-5.4-mini',
    provider: 'openai',
    tier: 'MID',
    context_tokens: 400_000,
    usd_input: 0.75,
    usd_output: 4.5,
    usd_cached: 0.08,
    description: 'OpenAI mid model — workhorse для gateway.',
  }),
  model({
    id: 'gpt-5.4',
    display_name: 'gpt-5.4',
    provider: 'openai',
    tier: 'FLAGSHIP',
    context_tokens: 400_000,
    usd_input: 2.5,
    usd_output: 15,
    usd_cached: 0.25,
    description: 'OpenAI flagship 5.4.',
  }),
  model({
    id: 'gpt-5',
    display_name: 'gpt-5',
    provider: 'openai',
    tier: 'MID',
    context_tokens: 400_000,
    usd_input: 1.25,
    usd_output: 10,
    usd_cached: 0.13,
    description: 'OpenAI mid; сильный баланс цена/качество.',
  }),
  model({
    id: 'gpt-5.5',
    display_name: 'gpt-5.5',
    provider: 'openai',
    tier: 'FLAGSHIP',
    context_tokens: 1_000_000,
    usd_input: 5,
    usd_output: 30,
    usd_cached: 0.5,
    description: 'OpenAI flagship (GPT-5.5, GA май 2026).',
  }),
  model({
    id: 'gpt-5.5-pro',
    display_name: 'gpt-5.5-pro',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 1_000_000,
    usd_input: 30,
    usd_output: 180,
    usd_cached: 3,
    description: 'OpenAI premium reasoning (GPT-5.5 Pro).',
  }),
  model({
    id: 'o3',
    display_name: 'o3',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 200_000,
    usd_input: 2,
    usd_output: 8,
    usd_cached: 0.5,
    description: 'OpenAI reasoning-модель.',
  }),
  model({
    id: 'o4-mini',
    display_name: 'o4-mini',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 200_000,
    usd_input: 1.1,
    usd_output: 4.4,
    usd_cached: 0.28,
    description: 'Дешёвый reasoning.',
  }),
  model({
    id: 'o1',
    display_name: 'o1',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 200_000,
    usd_input: 15,
    usd_output: 60,
    usd_cached: 7.5,
    description: 'OpenAI o1 — первое поколение premium reasoning, 200k ctx.',
  }),
  model({
    id: 'o1-mini',
    display_name: 'o1-mini',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 128_000,
    usd_input: 3,
    usd_output: 12,
    usd_cached: 1.5,
    description: 'OpenAI o1-mini — дешёвый reasoning, 128k ctx.',
  }),
  model({
    id: 'o3-mini',
    display_name: 'o3-mini',
    provider: 'openai',
    tier: 'PREMIUM',
    context_tokens: 200_000,
    usd_input: 1.1,
    usd_output: 4.4,
    usd_cached: 0.55,
    description: 'OpenAI o3-mini — дешёвый reasoning, 200k ctx.',
  }),
  model({
    id: 'gpt-4-turbo',
    display_name: 'gpt-4-turbo',
    provider: 'openai',
    tier: 'FLAGSHIP',
    context_tokens: 128_000,
    usd_input: 10,
    usd_output: 30,
    description: 'OpenAI GPT-4 Turbo (legacy, vision-capable).',
    vision: true,
  }),
  model({
    id: 'gpt-4',
    display_name: 'gpt-4',
    provider: 'openai',
    tier: 'FLAGSHIP',
    context_tokens: 8_192,
    usd_input: 30,
    usd_output: 60,
    description: 'OpenAI GPT-4 (legacy, 8k ctx).',
  }),
  model({
    id: 'gpt-3.5-turbo',
    display_name: 'gpt-3.5-turbo',
    provider: 'openai',
    tier: 'BUDGET',
    context_tokens: 16_385,
    usd_input: 0.5,
    usd_output: 1.5,
    description: 'OpenAI GPT-3.5 Turbo (legacy, 16k ctx).',
  }),
  model({
    id: 'gpt-4o',
    display_name: 'gpt-4o',
    provider: 'openai',
    tier: 'FLAGSHIP',
    context_tokens: 128_000,
    usd_input: 2.5,
    usd_output: 10,
    usd_cached: 1.25,
    description: 'OpenAI GPT-4o (omni, vision + audio).',
    vision: true,
  }),
  model({
    id: 'gpt-4o-mini',
    display_name: 'gpt-4o-mini',
    provider: 'openai',
    tier: 'MID',
    context_tokens: 128_000,
    usd_input: 0.15,
    usd_output: 0.6,
    usd_cached: 0.075,
    description: 'OpenAI GPT-4o-mini — дешёвый omni.',
    vision: true,
  }),

  // ---------- Anthropic (6) ----------
  model({
    id: 'claude-sonnet-4.6',
    display_name: 'claude-sonnet-4.6',
    provider: 'anthropic',
    tier: 'MID',
    context_tokens: 1_000_000,
    usd_input: 3,
    usd_output: 15,
    usd_cached: 0.3,
    description: 'Anthropic mid — самый популярный Claude.',
  }),
  model({
    id: 'claude-haiku-4.5',
    display_name: 'claude-haiku-4.5',
    provider: 'anthropic',
    tier: 'BUDGET',
    context_tokens: 200_000,
    usd_input: 1,
    usd_output: 5,
    usd_cached: 0.1,
    description: 'Anthropic fast & cheap.',
  }),
  model({
    id: 'claude-opus-4.7',
    display_name: 'claude-opus-4.7',
    provider: 'anthropic',
    tier: 'PREMIUM',
    context_tokens: 1_000_000,
    usd_input: 15,
    usd_output: 75,
    usd_cached: 1.5,
    description: 'Anthropic flagship.',
  }),
  model({
    id: 'claude-3.5-sonnet',
    display_name: 'claude-3.5-sonnet',
    provider: 'anthropic',
    tier: 'MID',
    context_tokens: 200_000,
    usd_input: 3,
    usd_output: 15,
    usd_cached: 0.3,
    description: 'Anthropic Claude 3.5 Sonnet (legacy).',
  }),
  model({
    id: 'claude-3.5-haiku',
    display_name: 'claude-3.5-haiku',
    provider: 'anthropic',
    tier: 'BUDGET',
    context_tokens: 200_000,
    usd_input: 0.8,
    usd_output: 4,
    usd_cached: 0.08,
    description: 'Anthropic Claude 3.5 Haiku (legacy budget).',
  }),
  model({
    id: 'claude-3-opus',
    display_name: 'claude-3-opus',
    provider: 'anthropic',
    tier: 'PREMIUM',
    context_tokens: 200_000,
    usd_input: 15,
    usd_output: 75,
    usd_cached: 1.5,
    description: 'Anthropic Claude 3 Opus (legacy premium).',
  }),

  // ---------- Google (7) ----------
  model({
    id: 'gemini-3-flash',
    display_name: 'gemini-3-flash',
    provider: 'google',
    tier: 'MID',
    context_tokens: 1_000_000,
    usd_input: 0.5,
    usd_output: 3,
    usd_cached: 0.05,
    description: 'Google mid (новый default Flash).',
  }),
  model({
    id: 'gemini-3.1-pro',
    display_name: 'gemini-3.1-pro',
    provider: 'google',
    tier: 'FLAGSHIP',
    context_tokens: 1_000_000,
    usd_input: 2,
    usd_output: 12,
    usd_cached: 0.2,
    description: 'Google flagship; >200k input биллится по верхнему tier.',
  }),
  model({
    id: 'gemini-2.5-flash',
    display_name: 'gemini-2.5-flash',
    provider: 'google',
    tier: 'MID',
    context_tokens: 1_000_000,
    usd_input: 0.3,
    usd_output: 2.5,
    usd_cached: 0.03,
    description: 'Google 2.5 Flash (предыдущее поколение Flash).',
  }),
  model({
    id: 'gemini-2.5-pro',
    display_name: 'gemini-2.5-pro',
    provider: 'google',
    tier: 'FLAGSHIP',
    context_tokens: 2_000_000,
    usd_input: 1.25,
    usd_output: 10,
    usd_cached: 0.13,
    description: 'Google Gemini 2.5 Pro (2M ctx, tiered).',
  }),
  model({
    id: 'gemini-1.5-flash-8b',
    display_name: 'gemini-1.5-flash-8b',
    provider: 'google',
    tier: 'NANO',
    context_tokens: 1_000_000,
    usd_input: 0.04,
    usd_output: 0.15,
    description: 'Google 1.5 Flash 8B — самая дешёвая модель Google.',
  }),
  model({
    id: 'gemini-1.5-pro',
    display_name: 'gemini-1.5-pro',
    provider: 'google',
    tier: 'FLAGSHIP',
    context_tokens: 2_000_000,
    usd_input: 1.25,
    usd_output: 5,
    usd_cached: 0.13,
    description: 'Google 1.5 Pro (legacy flagship, 2M ctx).',
  }),
  model({
    id: 'gemini-1.5-flash',
    display_name: 'gemini-1.5-flash',
    provider: 'google',
    tier: 'BUDGET',
    context_tokens: 1_000_000,
    usd_input: 0.075,
    usd_output: 0.3,
    description: 'Google 1.5 Flash — дешёвый workhorse, 1M ctx.',
  }),

  // ---------- DeepSeek (5) ----------
  model({
    id: 'deepseek-v3.2-chat',
    display_name: 'deepseek-v3.2-chat',
    provider: 'deepseek',
    tier: 'NANO',
    context_tokens: 128_000,
    usd_input: 0.28,
    usd_output: 0.42,
    usd_cached: 0.03,
    description:
      'DeepSeek v3.2 — DEPRECATED, снимается 2026-07-24. Мигрируйте на deepseek-v4-flash.',
    deprecated_at: '2026-07-24',
  }),
  model({
    id: 'deepseek-v4-flash',
    display_name: 'deepseek-v4-flash',
    provider: 'deepseek',
    tier: 'NANO',
    context_tokens: 1_000_000,
    usd_input: 0.14,
    usd_output: 0.28,
    usd_cached: 0.014,
    description:
      'DeepSeek V4 Flash — 284B/13B MoE, 1M ctx, default для auto:cheap.',
    best_for: ['Классификация', 'Короткие задачи', 'Лучшая цена/качество'],
  }),
  model({
    id: 'deepseek-v4-pro',
    display_name: 'deepseek-v4-pro',
    provider: 'deepseek',
    tier: 'MID',
    context_tokens: 1_000_000,
    usd_input: 1.74,
    usd_output: 3.48,
    usd_cached: 0.174,
    description: 'DeepSeek V4 Pro — 1.6T/49B MoE, 1M ctx.',
  }),
  model({
    id: 'deepseek-r1',
    display_name: 'deepseek-r1',
    provider: 'deepseek',
    tier: 'PREMIUM',
    context_tokens: 64_000,
    usd_input: 0.55,
    usd_output: 2.19,
    usd_cached: 0.14,
    description: 'DeepSeek R1 reasoning — прямой direct-DeepSeek endpoint.',
  }),
  model({
    id: 'deepseek-v3',
    display_name: 'deepseek-v3',
    provider: 'deepseek',
    tier: 'NANO',
    context_tokens: 64_000,
    usd_input: 0.28,
    usd_output: 0.42,
    usd_cached: 0.03,
    description:
      'DeepSeek v3 legacy alias (deepseek-chat) — снимается 2026-07-24.',
    deprecated_at: '2026-07-24',
  }),

  // ---------- Yandex (2) ----------
  model({
    id: 'yandexgpt-5.1-pro',
    display_name: 'yandexgpt-5.1-pro',
    provider: 'yandex',
    tier: 'MID',
    context_tokens: 32_000,
    usd_input: 6.56,
    usd_output: 6.56,
    description: 'Russian flagship; 152-FZ-friendly, хостинг в РФ.',
    streaming: false,
    tools: false,
    strict_json: false,
    ru_legal: true,
    prompt_caching: false,
  }),
  model({
    id: 'yandexgpt-5-lite',
    display_name: 'yandexgpt-5-lite',
    provider: 'yandex',
    tier: 'BUDGET',
    context_tokens: 32_000,
    usd_input: 1.64,
    usd_output: 1.64,
    description: 'Russian budget; 152-FZ-friendly, хостинг в РФ.',
    streaming: false,
    tools: false,
    strict_json: false,
    ru_legal: true,
    prompt_caching: false,
  }),

  // ---------- Sber (3) ----------
  model({
    id: 'gigachat-2-pro',
    display_name: 'gigachat-2-pro',
    provider: 'sber',
    tier: 'MID',
    context_tokens: 131_000,
    usd_input: 5.55,
    usd_output: 5.55,
    description: 'Sber GigaChat 2 Pro; 152-FZ-friendly.',
    tools: false,
    strict_json: false,
    ru_legal: true,
    prompt_caching: false,
  }),
  model({
    id: 'gigachat-2-lite',
    display_name: 'gigachat-2-lite',
    provider: 'sber',
    tier: 'BUDGET',
    context_tokens: 131_000,
    usd_input: 0.72,
    usd_output: 0.72,
    description: 'Sber GigaChat 2 (базовая); 152-FZ-friendly.',
    tools: false,
    strict_json: false,
    ru_legal: true,
    prompt_caching: false,
  }),
  model({
    id: 'gigachat-2-max',
    display_name: 'gigachat-2-max',
    provider: 'sber',
    tier: 'FLAGSHIP',
    context_tokens: 4_000_000,
    usd_input: 13.95,
    usd_output: 13.95,
    description:
      'Sber GigaChat 2 Max — premium tier, 4M ctx; 152-FZ-friendly.',
    tools: false,
    strict_json: false,
    ru_legal: true,
    prompt_caching: false,
  }),
];
