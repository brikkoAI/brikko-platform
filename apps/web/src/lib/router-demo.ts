/**
 * router-demo.ts — упрощённая клиентская копия Smart Router'а для лендинг-демо.
 *
 * Назначение: показать гостю сайта, КАК router классифицирует промпт и почему
 * выбирает конкретную модель — без реального вызова провайдера (бесплатно для нас,
 * мгновенно для пользователя). Полная серверная логика — в репозитории.
 *
 * Алгоритм намеренно прост и детерминирован, чтобы пользователь мог «прокликать»
 * 4 chips-примера и увидеть 4 разных результата без задержки.
 */

export type RouterCategory = 'chat' | 'reasoning' | 'code' | 'long_context';

export type RouterStrategy = 'auto:cheap' | 'auto:smart';

export interface ModelCard {
  id: string;
  provider: string;
  contextK: number;
  pricePerMillionRub: number;
}

export interface RouterDecision {
  category: RouterCategory;
  strategy: RouterStrategy;
  estimatedTokens: number;
  primary: ModelCard;
  fallback: ModelCard[];
  explanation: string;
}

const MODELS: Record<string, ModelCard> = {
  'deepseek-v3.2-chat': {
    id: 'deepseek-v3.2-chat',
    provider: 'DeepSeek',
    contextK: 128,
    pricePerMillionRub: 22,
  },
  'claude-haiku-4.5': {
    id: 'claude-haiku-4.5',
    provider: 'Anthropic',
    contextK: 200,
    pricePerMillionRub: 80,
  },
  'o4-mini': {
    id: 'o4-mini',
    provider: 'OpenAI',
    contextK: 128,
    pricePerMillionRub: 250,
  },
  'gpt-5.4-mini': {
    id: 'gpt-5.4-mini',
    provider: 'OpenAI',
    contextK: 128,
    pricePerMillionRub: 140,
  },
  'claude-sonnet-4.6': {
    id: 'claude-sonnet-4.6',
    provider: 'Anthropic',
    contextK: 200,
    pricePerMillionRub: 380,
  },
  'deepseek-v3.2-coder': {
    id: 'deepseek-v3.2-coder',
    provider: 'DeepSeek',
    contextK: 128,
    pricePerMillionRub: 30,
  },
  'gemini-3.1-pro': {
    id: 'gemini-3.1-pro',
    provider: 'Google',
    contextK: 2000,
    pricePerMillionRub: 320,
  },
  'gpt-5.5': {
    id: 'gpt-5.5',
    provider: 'OpenAI',
    contextK: 400,
    pricePerMillionRub: 480,
  },
};

const ROUTING_TABLE: Record<RouterCategory, { primary: string; fallback: string[] }> = {
  chat: {
    primary: 'deepseek-v3.2-chat',
    fallback: ['claude-haiku-4.5', 'gpt-5.4-mini'],
  },
  reasoning: {
    primary: 'o4-mini',
    fallback: ['gpt-5.4-mini', 'claude-haiku-4.5'],
  },
  code: {
    primary: 'claude-sonnet-4.6',
    fallback: ['deepseek-v3.2-coder', 'gpt-5.4-mini'],
  },
  long_context: {
    primary: 'gemini-3.1-pro',
    fallback: ['gpt-5.5', 'claude-sonnet-4.6'],
  },
};

/**
 * Грубая оценка токенов: 1 токен ≈ 3 символа кириллицы или ~4 латиницы.
 * Используем 3 для пессимистичной оценки (RU-первый рынок, чтобы long_context
 * срабатывал чуть раньше — это безопаснее для UX).
 */
function estimateTokens(input: string): number {
  return Math.ceil(input.length / 3);
}

function detectCode(input: string): boolean {
  if (input.includes('```')) return true;
  // 2+ строки с 4+ ведущими пробелами подряд = вероятный listing
  const lines = input.split('\n');
  let indented = 0;
  for (const line of lines) {
    if (/^ {4,}\S/.test(line)) {
      indented += 1;
      if (indented >= 2) return true;
    } else {
      indented = 0;
    }
  }
  return false;
}

function detectReasoning(input: string): boolean {
  const normalized = input.toLowerCase();
  const triggers = [
    'think step by step',
    'step by step',
    'шаг за шагом',
    'по шагам',
    'reasoning',
    'рассуждай',
    'решай по шагам',
  ];
  return triggers.some((t) => normalized.includes(t));
}

function classifyCategory(input: string, tokens: number): RouterCategory {
  if (detectCode(input)) return 'code';
  if (detectReasoning(input)) return 'reasoning';
  if (tokens > 50_000) return 'long_context';
  return 'chat';
}

function buildExplanation(
  category: RouterCategory,
  tokens: number,
  primary: ModelCard,
): string {
  switch (category) {
    case 'chat':
      return `Это короткая chat-задача без признаков reasoning или кода. Контекст ~${tokens.toLocaleString('ru-RU')} токенов уложился в ${primary.contextK}K. Strategy auto:cheap выбирает самую дешёвую совместимую модель — ${primary.id} от ${primary.provider} (${primary.pricePerMillionRub} ₽ / 1M токенов).`;
    case 'reasoning':
      return `Промпт содержит маркеры пошагового рассуждения («step by step», «по шагам»). Выбираем reasoning-модель ${primary.id} — она дороже chat-моделей, но даёт стабильно лучший результат на задачах с цепочкой выводов.`;
    case 'code':
      return `Промпт содержит code-fence или отступы — это код. Выбираем модель, заточенную под программирование: ${primary.id} от ${primary.provider}. На code-задачах она бьёт более дешёвые chat-модели по pass-rate.`;
    case 'long_context':
      return `Оценка ~${tokens.toLocaleString('ru-RU')} токенов превышает 50K — это long-context. Большинство chat-моделей не вытянут. Выбираем ${primary.id} с окном ${primary.contextK}K токенов.`;
  }
}

function lookupModel(id: string): ModelCard {
  const m = MODELS[id];
  if (!m) {
    // Этот путь возможен только при опечатке в ROUTING_TABLE — фейлим громко
    // в dev, в проде даём безопасный fallback, чтобы лендинг не падал.
    throw new Error(`router-demo: unknown model id "${id}"`);
  }
  return m;
}

export function classifyDemo(input: string): RouterDecision {
  const trimmed = input ?? '';
  const tokens = estimateTokens(trimmed);
  const category = classifyCategory(trimmed, tokens);
  const route = ROUTING_TABLE[category];
  const primary = lookupModel(route.primary);
  const fallback = route.fallback.map(lookupModel);

  return {
    category,
    strategy: 'auto:cheap',
    estimatedTokens: tokens,
    primary,
    fallback,
    explanation: buildExplanation(category, tokens, primary),
  };
}

export const CATEGORY_LABELS: Record<RouterCategory, string> = {
  chat: 'Chat',
  reasoning: 'Reasoning',
  code: 'Code',
  long_context: 'Long context',
};
