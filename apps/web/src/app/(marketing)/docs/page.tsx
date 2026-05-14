import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';

export const metadata: Metadata = {
  title: 'Документация Brikko — API, SDK, интеграции',
  description:
    'Quick start за 5 минут, OpenAI-совместимый API, нативный Anthropic Messages API, готовые рецепты, smart-routing.',
  alternates: { canonical: '/docs' },
};

/**
 * /docs — hub-страница документации (Cream Studio v6).
 *
 * UX-обоснование структуры:
 *   1) Editorial header с eyebrow «Документация» — задаёт регистр (это не
 *      маркетинг, это reference). 2 CTA inline — signup и cookbook.
 *   2) Quick Start идёт ВТОРЫМ. С трафика vc.ru первый вопрос — «насколько
 *      сложно подключить». Кодовый блок Python = ответ за 10 секунд.
 *   3) Endpoints — карточки в double-bezel, не таблица. На mobile удобнее
 *      читать последовательно + 2-строчное описание use-case под каждым.
 *   4) «Что дальше» — линки на детальные доки. Это hub, не reference;
 *      длинного контента нет, только тизеры с CTA.
 *
 * Стилистика — все цвета через CSS-vars (--bg-elevated / --hairline /
 * --code-bg / --code-fg). В dark theme code-блоки инвертируются автоматом
 * (bg=cream, fg=espresso) — реальный IDE-feel.
 *
 * SEO: ru-первый, цели — «brikko api», «brikko документация», «openai-
 * совместимый api в рублях», «anthropic messages api россия».
 */

const ENDPOINTS: ReadonlyArray<{
  method: 'POST' | 'GET';
  path: string;
  title: string;
  description: string;
  status?: 'available' | 'soon';
}> = [
  {
    method: 'POST',
    path: '/v1/chat/completions',
    title: 'OpenAI-совместимый Chat Completions',
    description:
      'Sync и streaming-режим, function calling, JSON mode. Полный контракт OpenAI Chat Completions — большинство SDK работают сменой base_url.',
  },
  {
    method: 'POST',
    path: '/v1/messages',
    title: 'Нативный Anthropic Messages API',
    description:
      'Полная схема Anthropic Messages: system, tools, content blocks, streaming через SSE. Нужен для Claude Code SDK и кода, написанного под anthropic SDK напрямую.',
  },
  {
    method: 'GET',
    path: '/v1/models',
    title: 'Список доступных моделей',
    description:
      '38 моделей от 6 провайдеров: ID, контекстное окно, capabilities, флаг 152-ФЗ. Используется для динамических селектов в админке клиента.',
  },
  {
    method: 'POST',
    path: '/v1/public/playground',
    title: 'Публичный sandbox без auth',
    description:
      'Для демо и оценки качества: 5 запросов в час и 15 в сутки на IP, без ключа. Cheap-tier модели, ограничение по длине промпта.',
  },
  {
    method: 'POST',
    path: '/v1/embeddings',
    title: 'Embeddings',
    description:
      'OpenAI-совместимый embeddings endpoint для RAG/семантического поиска. Модели text-embedding-3-small (1536-dim, ₽184/1M токенов) и text-embedding-3-large (3072-dim, ₽1196/1M токенов). Поддержка string и array input.',
  },
  {
    method: 'POST',
    path: '/v1/audio/transcriptions',
    title: 'Speech-to-Text (Whisper)',
    description:
      'OpenAI-совместимый STT endpoint. Whisper-1, multipart/form-data, файл до 25 MB, 50+ языков включая русский. Биллинг по минутам аудио (₽55.2/минута, округление вверх до 0.1 мин).',
  },
];

const NEXT_LINKS: ReadonlyArray<{
  href: Route;
  title: string;
  description: string;
  cta: string;
}> = [
  {
    href: '/docs/cookbook' as Route,
    title: 'Cookbook',
    description:
      'Пять production-ready рецептов: PII-маскинг договоров, классификация лидов, проверка эссе, follow-up email. Код + расчёт стоимости на 1000 запросов.',
    cta: 'Открыть Cookbook',
  },
  {
    href: '/docs/smart-routing' as Route,
    title: 'Smart Router',
    description:
      'Как работают auto:cheap / auto:smart / auto:fast / auto:ru-legal / auto:code. Классификация запросов, failover за <2 сек, заголовки x-router-decision.',
    cta: 'Открыть reference',
  },
  {
    href: '/integrations' as Route,
    title: 'Интеграции',
    description:
      'Готовые инструкции для Cursor, Claude Code, Codex CLI, Copilot CLI и Gemini CLI. Смена base_url — и AI-агент работает на рублёвом балансе.',
    cta: 'Все интеграции',
  },
  {
    href: '/models' as Route,
    title: 'Каталог моделей',
    description:
      '38 моделей от 6 провайдеров. Цены за 1M токенов (вход/выход), контекстное окно, признак 152-ФЗ-резидентности.',
    cta: 'Открыть каталог',
  },
  {
    href: '/faq' as Route,
    title: 'FAQ',
    description:
      'Юр-вопросы (152-ФЗ, документы, оплата для самозанятых), технические ограничения, биллинг и возвраты.',
    cta: 'Читать FAQ',
  },
];

const QUICK_START_CODE = `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-...",
    base_url="https://api.brikko.ru/v1",
)

resp = client.chat.completions.create(
    model="gpt-5.4-mini",
    messages=[{"role": "user", "content": "Привет"}],
)

print(resp.choices[0].message.content)`;

export default function DocsHubPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация
        </p>
        <h1 className="brikko-h1 mt-3">Документация Brikko</h1>
        <p className="brikko-lede mt-4">
          Brikko — OpenAI-совместимый gateway к 38 LLM от 6 провайдеров (OpenAI,
          Anthropic, Google, DeepSeek, Yandex, Sber) с рублёвой оплатой и
          закрывающими документами. Здесь — быстрый старт за 5 минут, справочник
          по endpoint&apos;ам, готовые рецепты под бизнес-задачи и инструкции по
          интеграциям с IDE.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/signup" className="brikko-cta-primary">
            <span>Получить ключ</span>
            <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
          </Link>
          <Link href={'/docs/cookbook' as Route} className="brikko-cta-secondary">
            Открыть Cookbook
          </Link>
        </div>
      </header>

      <section id="quick-start" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Quick Start (5 минут)</h2>
        <p className="brikko-prose mt-4">
          Если у вас уже есть код на OpenAI SDK — миграция занимает одну строку:
          смена <CodeInline>base_url</CodeInline>. Остальной контракт идентичен —
          те же поля, те же стримы, те же function calls.
        </p>

        <ol className="mt-6 space-y-3">
          <QuickStartStep
            n={1}
            title="Зарегистрируйся"
            body={
              <>
                После регистрации на баланс автоматически зачисляется{' '}
                <strong className="font-semibold text-fg-primary">
                  200 ₽ welcome-бонусом
                </strong>{' '}
                — это ~1 000 запросов к GPT-5.4 mini. Карта не нужна.
              </>
            }
          />
          <QuickStartStep
            n={2}
            title="Создай API-ключ"
            body={
              <>
                В разделе{' '}
                <Link href={'/app/keys' as Route} className="brikko-link">
                  /app/keys
                </Link>{' '}
                — кнопка «Создать ключ». Ключ показывается один раз, скопируй
                его в переменную окружения <CodeInline>BRIKKO_API_KEY</CodeInline>.
                Ротация — там же.
              </>
            }
          />
          <QuickStartStep
            n={3}
            title="Замени base_url в SDK"
            body={
              <>
                Один параметр в инициализации OpenAI-клиента. Никаких новых
                библиотек ставить не нужно.
              </>
            }
          />
        </ol>

        <CodeBlock label="Python (openai SDK)" code={QUICK_START_CODE} />

        <p className="brikko-prose mt-5">
          В примере используется конкретная модель{' '}
          <CodeInline>gpt-5.4-mini</CodeInline>. Если хотите, чтобы Brikko сам
          выбирал модель под запрос (по цене, скорости или по 152-ФЗ-периметру)
          — передайте <CodeInline>model=&quot;auto:cheap&quot;</CodeInline>.
          Подробнее — в{' '}
          <Link href={'/docs/smart-routing' as Route} className="brikko-link">
            документации Smart Router
          </Link>
          .
        </p>
      </section>

      <section id="auth" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Аутентификация</h2>
        <p className="brikko-prose mt-4">
          Все запросы (кроме публичного sandbox) требуют bearer-токен. Передавайте
          ключ в HTTP-заголовке:
        </p>
        <CodeBlock code={`Authorization: Bearer sk-brk-...`} />
        <p className="brikko-prose mt-4">
          Ключи выпускаются и ротируются в личном кабинете —{' '}
          <Link href={'/app/keys' as Route} className="brikko-link">
            /app/keys
          </Link>
          . Скомпрометированный ключ можно отозвать одним кликом, выпустив
          новый — старый перестаёт работать мгновенно. Каждому ключу можно дать
          имя (например, <CodeInline>prod-backend</CodeInline> или{' '}
          <CodeInline>staging</CodeInline>) — это удобно для аудита и аналитики
          потребления по проектам.
        </p>
      </section>

      <section id="endpoints" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Endpoints</h2>
        <p className="brikko-prose mt-4">
          Все endpoints — под базовым URL{' '}
          <CodeInline>https://api.brikko.ru</CodeInline>. Контракты совместимы с
          OpenAI и Anthropic — это позволяет переносить существующий код без
          изменений в логике.
        </p>

        <ul className="mt-6 space-y-4">
          {ENDPOINTS.map((ep) => (
            <li key={ep.path}>
              <EndpointCard {...ep} />
            </li>
          ))}
        </ul>
      </section>

      <section id="next" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Что дальше</h2>
        <p className="brikko-prose mt-4">
          Этот hub даёт минимум для первого запроса. Дальше — детальные разделы
          под конкретные задачи.
        </p>

        <ul className="mt-6 grid gap-4 md:grid-cols-2">
          {NEXT_LINKS.map((link) => (
            <li key={link.href} className="h-full">
              <Link href={link.href} className="brikko-card-link group h-full">
                <h3 className="text-lg font-semibold text-fg-primary transition-colors group-hover:text-fg-primary">
                  {link.title}
                </h3>
                <p className="mt-2 flex-1 text-body-sm text-fg-muted">
                  {link.description}
                </p>
                <span className="mt-4 inline-flex items-center gap-1 text-body-sm font-medium text-fg-primary">
                  {link.cta}
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
      </section>

      <section id="support" className="mt-16 scroll-mt-20">
        <h2 className="brikko-h2">Поддержка</h2>
        <p className="brikko-prose mt-4">
          По техническим вопросам, юр-документам и интеграциям —{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          . Время ответа зависит от тарифа: на Pay-as-you-go и Pro — best-effort
          в рабочие дни (обычно в течение 24 часов), на Team — приоритетная
          очередь, на Business и выше — выделенный менеджер и SLA по договору.
          Подробности — на странице{' '}
          <Link href={'/pricing' as Route} className="brikko-link">
            тарифов
          </Link>
          .
        </p>
      </section>

      <CtaCard />
    </article>
  );
}

/* ----------------------------------------------------------
 * Cream-themed local primitives
 * ---------------------------------------------------------- */

function QuickStartStep({
  n,
  title,
  body,
}: {
  n: number;
  title: string;
  body: React.ReactNode;
}) {
  return (
    <li className="brikko-card-flat flex gap-4">
      <div className="flex h-8 w-8 flex-none items-center justify-center rounded-full border border-[var(--hairline)] bg-[var(--bg-elevated)] font-mono text-body-sm font-semibold text-fg-primary">
        {n}
      </div>
      <div className="flex-1">
        <p className="font-semibold text-fg-primary">{title}</p>
        <p className="mt-1 text-body-sm text-fg-muted">{body}</p>
      </div>
    </li>
  );
}

function EndpointCard({
  method,
  path,
  title,
  description,
  status,
}: {
  method: 'POST' | 'GET';
  path: string;
  title: string;
  description: string;
  status?: 'available' | 'soon';
}) {
  const isSoon = status === 'soon';
  return (
    <div className={isSoon ? 'brikko-card-flat opacity-70' : 'brikko-card-flat'}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="brikko-pill font-mono">{method}</span>
        {/* Префикс api.brikko.ru обязателен в тексте: без него Googlebot
            резолвит относительно текущего хоста (brikko.ru/v1/audio/...)
            и попадает в 404 — закрывает GSC-варнинг "Not found (404)". */}
        <code className="font-mono text-body-sm font-semibold text-fg-primary">
          api.brikko.ru{path}
        </code>
        {isSoon ? <span className="brikko-pill">Скоро (V2)</span> : null}
      </div>
      <p className="mt-3 font-semibold text-fg-primary">{title}</p>
      <p className="mt-1 text-body-sm text-fg-muted">{description}</p>
    </div>
  );
}

function CodeInline({ children }: { children: React.ReactNode }) {
  return <code className="brikko-code-inline">{children}</code>;
}

function CodeBlock({ label, code }: { label?: string; code: string }) {
  return (
    <div className="mt-6">
      {label ? (
        <p className="mb-2 text-body-sm font-medium text-fg-muted">{label}</p>
      ) : null}
      <div className="brikko-code-shell">
        <pre className="brikko-code-pre">
          <code className="font-mono">{code}</code>
        </pre>
      </div>
    </div>
  );
}

function CtaCard() {
  return (
    <div className="brikko-cta-card mt-16">
      <p className="flex-1 text-body text-fg-primary">
        Получи API-ключ — стартовые 200&nbsp;₽ в баланс, карта не нужна. На
        welcome-бонусе можно протестировать все 38 моделей и Smart Router.
      </p>
      <Link href="/signup" className="brikko-cta-primary">
        <span>Получить ключ</span>
        <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
      </Link>
    </div>
  );
}
