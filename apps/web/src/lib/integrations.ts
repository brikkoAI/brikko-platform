/**
 * Каталог интеграций Brikko с AI-coding-агентами (Cursor, Claude Code,
 * Codex CLI, Copilot CLI, Gemini CLI). Distribution-play: Brikko позиционируется
 * как backend для всех IDE-агентов сразу — пользователь меняет один base_url
 * и получает рублёвый счёт + failover.
 *
 * Используется на:
 *   - /integrations (index, сетка карточек)
 *   - /integrations/[slug] (детальная страница)
 *   - / (секция Integrations на лендинге, между Features и SmartRouterDemo)
 *   - /sitemap.xml
 *
 * Данные намеренно лежат в TS-модуле, не в MD/JSON: страниц всего 5,
 * контент стабилен (это not blog), нет смысла тащить лишний markdown-pipeline.
 * Расширим до 10+ интеграций → переедем в .md как cookbook (см. lib/cookbook.ts).
 */

import type { LucideIcon } from 'lucide-react';
import {
  Bot,
  Boxes,
  GitBranch,
  Sparkles,
  Terminal,
} from 'lucide-react';

export type IntegrationSlug =
  | 'cursor'
  | 'claude-code'
  | 'openai-codex'
  | 'github-copilot'
  | 'gemini-cli';

export interface IntegrationSetupStep {
  /** Заголовок шага (одна строка). */
  title: string;
  /** Подробное пояснение (1-2 предложения), без HTML. */
  body: string;
  /** Опциональный код-блок: shell-команды или env-переменные. */
  code?: string;
  /** Подпись над кодом (например, "Linux/macOS" или ".env"). */
  codeLabel?: string;
}

export interface IntegrationCostExample {
  /** Сценарий ("1000 запросов GPT-5 mini" и т.п.). */
  scenario: string;
  /** Итоговая стоимость в ₽. */
  rub: number;
  /** Что происходит за это (1 строка). */
  detail: string;
}

export interface Integration {
  slug: IntegrationSlug;
  /** Короткое название для карточек ("Cursor", "Claude Code"). */
  shortName: string;
  /** H1 страницы. */
  title: string;
  /** Один параграф под H1 (зачем эта интеграция). */
  intro: string;
  /** Meta description (≤ 160 символов). */
  metaDescription: string;
  /** Подзаголовок для карточки на /integrations и в секции лендинга. */
  cardSubtitle: string;
  /** Иконка lucide для карточек (логотипов IDE в public/ нет, см. PR). */
  icon: LucideIcon;
  /**
   * Endpoint, который пользователь должен прописать в IDE.
   * Для Anthropic-формата (Claude Code) — /v1/anthropic; остальные — /v1.
   */
  endpoint: string;
  /** Формат API: совместимость диктует, какие env-переменные ставить. */
  apiFormat: 'openai' | 'anthropic' | 'google';
  /** 3 буллета "зачем подключать через Brikko". */
  whyBullets: [string, string, string];
  /** Шаги установки (2-4 шт). */
  setupSteps: IntegrationSetupStep[];
  /** Конкретные расчёты под этого пользователя (2 примера). */
  costExamples: [IntegrationCostExample, IntegrationCostExample];
  /** Заметка про failover/smart routing — что произойдёт при сбое. */
  failoverNote: string;
}

const COMMON_WHY: Integration['whyBullets'] = [
  'Оплата в рублях через ЮKassa, чек самозанятого после каждого пополнения.',
  '6 провайдеров на одном ключе: OpenAI, Anthropic, Google, DeepSeek, YandexGPT, GigaChat.',
  'Failover на резервного провайдера за <2 секунды если основной упал.',
];

export const INTEGRATIONS: ReadonlyArray<Integration> = [
  {
    slug: 'cursor',
    shortName: 'Cursor',
    title: 'Cursor + Brikko: GPT/Claude в России без VPN',
    intro:
      'Cursor — IDE с встроенным AI-агентом, по умолчанию ходит в OpenAI напрямую. ' +
      'Через Brikko ты получаешь тот же GPT-5 и Claude, но платишь рублями с расчётного счёта или ' +
      'ИП-карты, без VPN и без риска блокировки на стороне OpenAI.',
    metaDescription:
      'Подключи Cursor IDE к Brikko за 30 секунд. Все модели (GPT-5, Claude, DeepSeek), оплата в рублях, чек самозанятого.',
    cardSubtitle: 'Custom OpenAI URL — 30 секунд настройки',
    icon: Sparkles,
    endpoint: 'https://api.brikko.ru/v1',
    apiFormat: 'openai',
    whyBullets: COMMON_WHY,
    setupSteps: [
      {
        title: 'Получи API-ключ Brikko',
        body: 'Регистрация в личном кабинете занимает 1 минуту, на баланс сразу зачисляется 200 ₽ welcome-бонуса (без ввода карты).',
      },
      {
        title: 'Открой Cursor → Settings → Models',
        body: 'В разделе "OpenAI API Key" вставь свой ключ Brikko (формат sk-brk-...). Ниже разверни блок "Override OpenAI Base URL" и впиши endpoint.',
        codeLabel: 'Override OpenAI Base URL',
        code: 'https://api.brikko.ru/v1',
      },
      {
        title: 'Включи нужные модели в списке',
        body: 'Cursor проверит endpoint и подхватит модели. Включи galaxy gpt-5, claude-sonnet-4.6, deepseek-chat — этого достаточно для большинства задач. Полный список — на /models.',
      },
      {
        title: 'Готово — пиши код',
        body: 'Cmd/Ctrl+K, Cmd/Ctrl+L и Composer работают как раньше. Расход смотри в Brikko-кабинете в реальном времени.',
      },
    ],
    costExamples: [
      {
        scenario: '1000 диалогов с Cursor Composer на claude-sonnet-4.6',
        rub: 720,
        detail: 'Средний диалог ~3k input + 1k output токенов.',
      },
      {
        scenario: '1000 быстрых правок Cmd+K на deepseek-v3.2-chat',
        rub: 14,
        detail: '~500 input + 200 output токенов на запрос.',
      },
    ],
    failoverNote:
      'Если OpenAI или Anthropic упали — Brikko автоматически перенаправит запрос на DeepSeek или YandexGPT. ' +
      'Cursor ничего не заметит, ты увидишь пометку в логах кабинета.',
  },

  {
    slug: 'claude-code',
    shortName: 'Claude Code',
    title: 'Claude Code + Brikko: оплата в рублях',
    intro:
      'Claude Code — официальный CLI-агент Anthropic. По умолчанию требует подписку Anthropic (доллары, ' +
      'международная карта). Через Brikko достаточно поменять две переменные окружения — ' +
      'и тот же Claude Sonnet/Opus работает на рублёвом балансе.',
    metaDescription:
      'Запусти Anthropic Claude Code через Brikko gateway. Поменяй ANTHROPIC_BASE_URL и платишь рублями.',
    cardSubtitle: 'Anthropic-совместимый endpoint, две env-переменные',
    icon: Bot,
    endpoint: 'https://api.brikko.ru/v1/anthropic',
    apiFormat: 'anthropic',
    whyBullets: COMMON_WHY,
    setupSteps: [
      {
        title: 'Получи ключ Brikko',
        body: 'Зарегистрируйся, выпусти ключ в /app/keys. Welcome-бонуса 200 ₽ хватит на ~80 диалогов с Sonnet или ~20 с Opus.',
      },
      {
        title: 'Установи Claude Code',
        body: 'Если ещё не установлен — стандартный npm-пакет от Anthropic.',
        codeLabel: 'shell',
        code: 'npm install -g @anthropic-ai/claude-code',
      },
      {
        title: 'Поставь env-переменные',
        body: 'Brikko эмулирует Anthropic API на /v1/anthropic — клиент Claude Code ходит туда вместо api.anthropic.com.',
        codeLabel: 'shell (Linux/macOS)',
        code: 'export ANTHROPIC_BASE_URL="https://api.brikko.ru/v1/anthropic"\nexport ANTHROPIC_API_KEY="sk-brk-..."',
      },
      {
        title: 'Запусти claude в проекте',
        body: 'Любой проект, любая команда — claude --print, claude --resume, claude /init. Расход списывается с рублёвого баланса по нашему прайсу — он открыт на /models.',
        codeLabel: 'shell',
        code: 'cd ~/projects/my-app\nclaude',
      },
    ],
    costExamples: [
      {
        scenario: '100 task-runs Claude Sonnet 4.6 на средний проект',
        rub: 2400,
        detail: 'Средний run ~30k input + 8k output токенов с tool calls.',
      },
      {
        scenario: '20 глубоких рефакторингов Opus 4.7',
        rub: 4800,
        detail: 'Большой контекст (~100k input + 15k output).',
      },
    ],
    failoverNote:
      'Anthropic-формат пока без cross-provider failover (нельзя отдать Claude-запрос в OpenAI без ' +
      'трансляции tool-calls). При сбое Anthropic мы держим запрос в очереди до 60 секунд и автоматически ' +
      'ретраим — детали в /docs/smart-routing.',
  },

  {
    slug: 'openai-codex',
    shortName: 'Codex CLI',
    title: 'OpenAI Codex CLI с рублёвой оплатой',
    intro:
      'Codex CLI — официальный агент OpenAI для работы с кодом из терминала. Полностью совместим с ' +
      'OpenAI Chat Completions API, поэтому Brikko встаёт прозрачно: одна переменная окружения и ' +
      'та же модель работает на рублёвом балансе.',
    metaDescription:
      'Подключи Codex CLI к нашему API за 1 минуту. Совместимость 100%, без VPN, чек для бухгалтерии.',
    cardSubtitle: 'OPENAI_BASE_URL — и Codex работает',
    icon: Terminal,
    endpoint: 'https://api.brikko.ru/v1',
    apiFormat: 'openai',
    whyBullets: COMMON_WHY,
    setupSteps: [
      {
        title: 'Получи ключ Brikko',
        body: 'sk-brk-... в кабинете /app/keys. Если в команде несколько разработчиков — выпусти разные ключи на каждого, лимиты настраиваются индивидуально.',
      },
      {
        title: 'Установи Codex CLI',
        body: 'Стандартная установка из npm.',
        codeLabel: 'shell',
        code: 'npm install -g @openai/codex',
      },
      {
        title: 'Перенаправь base_url',
        body: 'Codex читает стандартные OPENAI_* env-переменные. Достаточно поменять две.',
        codeLabel: 'shell (Linux/macOS)',
        code: 'export OPENAI_BASE_URL="https://api.brikko.ru/v1"\nexport OPENAI_API_KEY="sk-brk-..."',
      },
      {
        title: 'Запусти codex',
        body: 'Все режимы — interactive, --suggest, --auto-edit, --full-auto — работают как у OpenAI напрямую. Стриминг и tool calling включены.',
        codeLabel: 'shell',
        code: 'codex "добавь тесты к функции parseDate"',
      },
    ],
    costExamples: [
      {
        scenario: '1000 коротких правок Codex на gpt-5-mini',
        rub: 96,
        detail: '~2k input + 800 output токенов на правку.',
      },
      {
        scenario: '200 full-auto сессий на gpt-5',
        rub: 1840,
        detail: 'Средняя сессия ~15k input + 4k output.',
      },
    ],
    failoverNote:
      'OpenAI-формат поддерживает кросс-провайдерный failover: если у OpenAI инцидент, Brikko ' +
      'автоматически проксирует запрос на Anthropic Claude или DeepSeek (сохраняя tool calls). ' +
      'Codex CLI получит тот же формат ответа и продолжит работу.',
  },

  {
    slug: 'github-copilot',
    shortName: 'Copilot CLI',
    title: 'GitHub Copilot CLI через Brikko',
    intro:
      'GitHub Copilot CLI поддерживает custom-провайдеров для моделей через GITHUB_COPILOT_MODEL_PROVIDER. ' +
      'Brikko встаёт как OpenAI-совместимый endpoint — и в Copilot CLI становятся доступны не только ' +
      'GPT, но и Claude, Gemini, DeepSeek с рублёвой оплатой.',
    metaDescription:
      'Используй Copilot CLI с любой моделью (GPT/Claude/Gemini) через рублёвый счёт.',
    cardSubtitle: 'Расширь Copilot CLI до 38 моделей',
    icon: GitBranch,
    endpoint: 'https://api.brikko.ru/v1',
    apiFormat: 'openai',
    whyBullets: COMMON_WHY,
    setupSteps: [
      {
        title: 'Получи ключ Brikko',
        body: 'Зарегистрируйся, открой /app/keys, скопируй sk-brk-...',
      },
      {
        title: 'Установи Copilot CLI',
        body: 'Расширение к gh CLI от GitHub.',
        codeLabel: 'shell',
        code: 'gh extension install github/gh-copilot',
      },
      {
        title: 'Сконфигурируй custom endpoint',
        body: 'Copilot CLI читает env-переменные провайдера. Brikko эмулирует OpenAI-API, поэтому подойдут стандартные OPENAI_*.',
        codeLabel: 'shell',
        code: 'export OPENAI_BASE_URL="https://api.brikko.ru/v1"\nexport OPENAI_API_KEY="sk-brk-..."\nexport GH_COPILOT_MODEL="gpt-5-mini"',
      },
      {
        title: 'Запрашивай команды',
        body: 'gh copilot suggest и gh copilot explain работают как раньше, но расход идёт через Brikko-баланс.',
        codeLabel: 'shell',
        code: 'gh copilot suggest "найди файлы больше 50 МБ"',
      },
    ],
    costExamples: [
      {
        scenario: '5000 suggest-запросов на gpt-5-mini',
        rub: 240,
        detail: 'Короткий промпт ~600 input + 200 output токенов.',
      },
      {
        scenario: '500 explain-запросов на claude-haiku-4.5',
        rub: 38,
        detail: '~1.5k input + 600 output на разбор команды.',
      },
    ],
    failoverNote:
      'При сбое выбранного провайдера Brikko прозрачно отдаст ответ из резервного (см. /docs/smart-routing). ' +
      'Copilot CLI не отличит, лог в кабинете покажет, что запрос ушёл, например, в claude-sonnet-4.6 вместо gpt-5-mini.',
  },

  {
    slug: 'gemini-cli',
    shortName: 'Gemini CLI',
    title: 'Google Gemini CLI с DeepSeek-fallback',
    intro:
      'Gemini CLI — официальный CLI-агент Google с поддержкой кастомных endpoint через ' +
      'GEMINI_API_BASE. Brikko прокидывает Google AI API, добавляя поверх рублёвую оплату и ' +
      'failover на DeepSeek/Claude если Google недоступен (а это в РФ бывает).',
    metaDescription:
      'Gemini CLI + Brikko gateway: failover на DeepSeek если Google недоступен, оплата рублями.',
    cardSubtitle: 'Gemini + DeepSeek-fallback из коробки',
    icon: Boxes,
    endpoint: 'https://api.brikko.ru/v1',
    apiFormat: 'google',
    whyBullets: [
      'Оплата в рублях через ЮKassa, чек самозанятого после каждого пополнения.',
      'DeepSeek-fallback: если Google недоступен, запрос отдаётся в deepseek-v3.2-chat без потери tool-calls.',
      'Доступ к 38 моделям через тот же ключ — переключаешь модель в одном CLI.',
    ],
    setupSteps: [
      {
        title: 'Получи ключ Brikko',
        body: 'sk-brk-... в /app/keys. Welcome-бонус 200 ₽ ≈ 1500 запросов к Gemini Flash.',
      },
      {
        title: 'Установи Gemini CLI',
        body: 'Через npm от Google.',
        codeLabel: 'shell',
        code: 'npm install -g @google/gemini-cli',
      },
      {
        title: 'Перенаправь GEMINI_API_BASE',
        body: 'Brikko-endpoint поддерживает Google AI Generative Language v1beta-формат. Ключ берётся из GEMINI_API_KEY.',
        codeLabel: 'shell (Linux/macOS)',
        code: 'export GEMINI_API_BASE="https://api.brikko.ru/v1"\nexport GEMINI_API_KEY="sk-brk-..."',
      },
      {
        title: 'Запусти gemini',
        body: 'Все команды CLI работают штатно. Failover включён по умолчанию — отключить можно header-ом X-Brikko-Failover: off.',
        codeLabel: 'shell',
        code: 'gemini "перепиши README.md в формате diataxis"',
      },
    ],
    costExamples: [
      {
        scenario: '1000 запросов на gemini-3-flash',
        rub: 130,
        detail: '~3k input + 1k output на запрос.',
      },
      {
        scenario: '200 длинных задач на gemini-3.1-pro',
        rub: 880,
        detail: 'Большой контекст (~50k input + 5k output).',
      },
    ],
    failoverNote:
      'Google AI Studio периодически режет трафик из РФ-IP. При HTTP 451/403 от Google Brikko ' +
      'автоматически перенаправит запрос на deepseek-v3.2-chat — формат ответа сохраняется, ' +
      'CLI продолжит работу. Подробнее в /docs/smart-routing.',
  },
];

const INTEGRATION_BY_SLUG = new Map<IntegrationSlug, Integration>(
  INTEGRATIONS.map((i) => [i.slug, i] as const),
);

export function getIntegration(slug: string): Integration | undefined {
  return INTEGRATION_BY_SLUG.get(slug as IntegrationSlug);
}

export function getIntegrationSlugs(): ReadonlyArray<IntegrationSlug> {
  return INTEGRATIONS.map((i) => i.slug);
}
