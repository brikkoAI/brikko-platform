import Link from 'next/link';
import type { Route } from 'next';
import { ArrowRight } from 'lucide-react';

export const metadata = {
  title: 'Smart Router — как Brikko выбирает модель за вас · Brikko',
  description:
    'Документация Smart Router: 4 стратегии (auto:cheap / auto:smart / auto:fast / auto:ru-legal), классификация запросов, failover за <2 сек, заголовки x-router-decision. Готовый код Python / curl / TypeScript.',
  alternates: { canonical: '/docs/smart-routing' },
} as const;

/**
 * /docs/smart-routing — техническая документация router'а (Cream Studio v6).
 *
 * UX-обоснование структуры:
 *   1) Hero отвечает на «зачем эта страница» одним абзацем.
 *   2) §1 объясняет роутер на пальцах ДО таблиц и кода — для не-инженеров.
 *   3) §2 «4 стратегии» — главная reference-таблица. Кладём раньше всего
 *      технического, чтобы человек, которого интересует только «какой
 *      auto:* выбрать», нашёл ответ в первые 30 секунд.
 *   4) §3-§4 — внутренности (классификатор, failover) для тех, кто читает
 *      дальше. Цифры (50_000 токенов, 2 сек failover, 5 паттернов
 *      reasoning) приведены ИЗ реального кода gateway, не из маркетинга.
 *   5) §5 — три кодовых блока: Python первым (наш основной сегмент в РФ),
 *      curl вторым (CTO-проверка), TypeScript последним. Все — в cream
 *      code-shell (espresso bg в light, cream bg в dark — IDE-feel).
 *   6) §6 — заголовки ответа. Без этой секции инженер не сможет дебажить.
 */

const TABLE_OF_CONTENTS = [
  { id: 'what', title: 'Что делает Smart Router' },
  { id: 'strategies', title: '4 стратегии: auto:cheap / smart / fast / ru-legal' },
  { id: 'classifier', title: 'Как router классифицирует запрос' },
  { id: 'failover', title: 'Failover за <2 секунд' },
  { id: 'usage', title: 'Как использовать' },
  { id: 'headers', title: 'Заголовки ответа' },
];

export default function SmartRoutingDocPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Документация
        </p>
        <h1 className="brikko-h1 mt-3">
          Smart Router — как Brikko выбирает модель за вас
        </h1>
        <p className="brikko-lede mt-4">
          Один параметр <CodeInline>model: &quot;auto:cheap&quot;</CodeInline> — и
          вместо 38 моделей в каталоге решение принимает router. Берёт самую
          дешёвую (или самую быструю / умную / РФ-локальную) модель, способную
          справиться с конкретным запросом, и держит наготове резервную цепочку на
          случай сбоя провайдера. Эта страница объясняет, по какой логике он это
          делает.
        </p>
      </header>

      <nav
        aria-label="Содержание"
        className="mt-8 brikko-card-flat"
      >
        <p className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
          На странице
        </p>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-body-sm text-fg-muted marker:text-fg-faint">
          {TABLE_OF_CONTENTS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="brikko-link">
                {s.title}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <section id="what" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">1. Что делает Smart Router</h2>
        <p className="brikko-prose mt-4">
          Smart Router — серверный компонент Brikko, который преобразует псевдо-имя
          модели вида <CodeInline>auto:cheap</CodeInline> в конкретную модель из{' '}
          <Link href={'/models' as Route} className="brikko-link">
            каталога 38 LLM
          </Link>{' '}
          до того, как мы отправим запрос провайдеру. Решение принимается за
          единицы миллисекунд: класс задачи определяется регулярками и порогами по
          длине входа, фильтр eligible-моделей собирается из каталога с проверкой
          контекстного окна и capabilities, цепочка fallback&apos;ов выстраивается
          по приоритету стратегии. Никакого ML на горячем пути — это намеренно:
          ошибка классификации деградирует к более дорогой модели, никогда — к
          провалу запроса.
        </p>
        <p className="brikko-prose mt-4">
          Пользователь видит в ответе единый шейп OpenAI Chat Completions, плюс
          три служебных заголовка с трассировкой решения. Если основной провайдер
          вернул 5xx, таймаут или rate-limit — router молча уходит в резерв и
          приклеивает к ответу заголовок{' '}
          <CodeInline>x-router-fallback-used: true</CodeInline>. С точки зрения
          вашего кода — обычный успешный ответ.
        </p>
      </section>

      <section id="strategies" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">
          2. 4 стратегии: auto:cheap / auto:smart / auto:fast / auto:ru-legal
        </h2>
        <p className="brikko-prose mt-4">
          Стратегия — это <em>правило сортировки</em> подходящих моделей. Router
          сначала отбирает eligible-кандидатов (контекст, capabilities,
          152-ФЗ-флаг), затем сортирует их по правилу стратегии и берёт первого
          живого. Все четыре стратегии deterministic: один и тот же запрос на
          разных подах кластера вернёт одну и ту же primary-модель.
        </p>

        <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
          <table className="w-full border-collapse text-body-sm">
            <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
              <tr>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Стратегия
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Что оптимизирует
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Когда брать
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              <tr>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top font-mono text-body-sm">
                  auto:cheap
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Минимальная цена при микшировании 70% input / 30% output.
                  Дефолтная стратегия.
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Классификация, экстракция, suggest-фичи, бэк-офис. Высокий
                  объём, цена решает.
                </td>
              </tr>
              <tr>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top font-mono text-body-sm">
                  auto:smart
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Качество на копейку: <code>quality_score / price</code>. На
                  chat и code исключаем PREMIUM-tier (Opus, o-series) — они в
                  5–10× дороже за маржинальный прирост. На reasoning — наоборот,
                  PREMIUM остаётся.
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Анализ договоров, сложный reasoning, RAG c длинным контекстом.
                </td>
              </tr>
              <tr>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top font-mono text-body-sm">
                  auto:fast
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Минимальный <code>latency_p50_ms</code>. Тай-брейк — цена.
                  PREMIUM-tier не исключаем: иногда самый быстрый ответ — у
                  flagship-модели в менее загруженном регионе.
                </td>
                <td className="border-b border-[var(--hairline)] px-4 py-3 align-top">
                  Чат-интерфейсы, copilot-suggest, autocomplete. Где P50 &lt;1
                  сек важнее цены.
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 align-top font-mono text-body-sm">
                  auto:ru-legal
                </td>
                <td className="px-4 py-3 align-top">
                  Только модели на территории РФ (Yandex / Sber). Сортировка по
                  цене, как у <code>auto:cheap</code>. Если все RU-провайдеры
                  лежат — отдаём 503, не уходим за границу даже на фоллбэке.
                </td>
                <td className="px-4 py-3 align-top">
                  152-ФЗ-чувствительные данные: персональные данные клиентов,
                  медданные, гос-сегмент. Compliance &gt; цены.
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p className="brikko-prose mt-5">
          Параметры окружения, которые роутер учитывает на верхнем слое:{' '}
          <CodeInline>require_tools=true</CodeInline> (если в запросе есть{' '}
          <CodeInline>tools[]</CodeInline>, отбрасываем модели без
          function-calling), <CodeInline>exclude_providers</CodeInline> (вы
          можете запинить уровнем аккаунта, что DeepSeek недоступен — например,
          если ваш compliance-офицер этого требует) и контекстное окно (модель
          выбывает, если в неё не влезает <CodeInline>input + 1k</CodeInline>{' '}
          токенов запаса под выход).
        </p>
      </section>

      <section id="classifier" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">3. Как router классифицирует запрос</h2>
        <p className="brikko-prose mt-4">
          Каждый запрос router раскладывает в одну из четырёх внутренних
          категорий. Это не пользовательский интерфейс — категории нужны
          стратегиям, чтобы не давать модели на 32k-контексте 100k-токенный
          RAG-дамп. Логика намеренно простая (regex + пороги): мы не строим
          классификатор на горячем пути, ошибки деградируют к более дорогой
          модели, никогда — к провалу.
        </p>

        <div className="mt-6 space-y-4">
          <ClassifierCard
            label="long_context"
            heuristic={
              <>
                <CodeInline>estimated_input_tokens ≥ 50&nbsp;000</CodeInline>.
                Жёсткий порог из{' '}
                <CodeInline>LONG_CONTEXT_TOKEN_THRESHOLD</CodeInline>.
              </>
            }
            why="Выбран как граница, выше которой нельзя оставаться в 32k-окне Yandex/Sber. Запрос автоматически уходит на модели с 200k+ контекстом (Claude Sonnet/Opus, Gemini, GPT-5)."
          />
          <ClassifierCard
            label="reasoning"
            heuristic={
              <>
                Текст содержит одну из 5 фраз-маркеров CoT:{' '}
                <code className="font-mono text-body-sm">«think step by step»</code>,{' '}
                <code className="font-mono text-body-sm">«chain of thought»</code>,{' '}
                <code className="font-mono text-body-sm">«reason carefully»</code>,{' '}
                <code className="font-mono text-body-sm">«пошагово рассуждай»</code>,{' '}
                <code className="font-mono text-body-sm">«цепочка рассуждений»</code>.
                Также — если клиент явно прислал{' '}
                <code className="font-mono text-body-sm">reasoning_effort: medium|high</code>{' '}
                (поле OpenAI-совместимого API).
              </>
            }
            why="Триггеры выбраны консервативно — false positive дорогой (даём o-серию там, где справится Sonnet). Если ваш CoT-запрос не попал — добавьте «рассуждай по шагам» в начало промпта."
          />
          <ClassifierCard
            label="code"
            heuristic={
              <>
                Markdown code-fence{' '}
                <code className="font-mono text-body-sm">```...```</code> или{' '}
                3+ строки с отступом ≥4 пробела (regex{' '}
                <code className="font-mono text-body-sm">_CODE_FENCE_RE</code>).
              </>
            }
            why="Мы НЕ классифицируем «code» по упоминанию слова «функция» или «класс» — слишком много ложных срабатываний на бизнес-текстах. Если хотите гарантированно code-режим — оборачивайте сниппет в ``` или пиньте модель напрямую."
          />
          <ClassifierCard
            label="chat"
            heuristic="Дефолт. Не сработали другие три проверки."
            why="Самая дешёвая в плане сортировки — ставит cheap-tier (DeepSeek, GigaChat Lite, Gemini Flash) первыми. 80% B2B-трафика по нашим логам попадает сюда."
          />
        </div>

        <p className="brikko-prose mt-6">
          Порядок проверок внутри классификатора зашит:{' '}
          <strong className="font-semibold text-fg-primary">
            long_context → reasoning_effort → reasoning-фразы → code → chat
          </strong>
          . Это значит, что 100k-токенный код всё равно поедет как long_context, а
          не как code — выбор модели делается с учётом контекстного окна, а
          200k-капабельные модели и так умеют код.
        </p>
      </section>

      <section id="failover" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">4. Failover за &lt;2 секунд</h2>
        <p className="brikko-prose mt-4">
          После выбора стратегии router возвращает не одну модель, а упорядоченную
          цепочку: primary плюс 2–4 fallback&apos;а с предпочтением разных
          провайдеров. Если primary отдал HTTP 5xx, провайдерскую ошибку
          rate-limit, или не вернул первый токен за 8 секунд — мы немедленно
          стартуем запрос к следующей модели в цепочке. Полный бюджет на switch —
          ровно 2 секунды: setup TLS-соединения через keep-alive pool, заголовки,
          первый байт ответа.
        </p>
        <p className="brikko-prose mt-4">
          Внутренний circuit-breaker запоминает падение провайдера на 30 секунд:
          если OpenAI вернул 503 на одном запросе, следующий запрос той же
          стратегии пропустит OpenAI вообще и пойдёт сразу на Anthropic (без
          штрафа в 8 секунд ожидания). Это спасает в момент массовых сбоев —
          типично OpenAI или Anthropic ложатся целым регионом на 5–15 минут раз в
          квартал.
        </p>

        <div className="brikko-card-bezel mt-6">
          <div className="brikko-card-bezel-inner">
            <p className="text-body-sm font-medium uppercase tracking-wide text-fg-muted">
              Конкретный пример
            </p>
            <p className="brikko-prose mt-2">
              Запрос со стратегией <CodeInline>auto:fast</CodeInline> на категорию{' '}
              <CodeInline>chat</CodeInline>. Цепочка на сегодня:{' '}
              <strong className="font-mono text-fg-primary">gpt-5-4-mini</strong>{' '}
              →{' '}
              <strong className="font-mono text-fg-primary">claude-haiku-4-5</strong>{' '}
              →{' '}
              <strong className="font-mono text-fg-primary">gemini-3-flash</strong>{' '}
              →{' '}
              <strong className="font-mono text-fg-primary">deepseek-v3-2-chat</strong>
              . OpenAI падает с 5xx на <CodeInline>gpt-5-4-mini</CodeInline> (это
              бывает) — router в течение ~1.7 сек открывает соединение к
              Anthropic, отправляет тот же payload в{' '}
              <CodeInline>claude-haiku-4-5</CodeInline>, и клиент получает
              успешный ответ. В заголовках:{' '}
              <CodeInline>x-router-fallback-used: true</CodeInline>,{' '}
              <CodeInline>x-router-decision: claude-haiku-4-5</CodeInline>.
            </p>
          </div>
        </div>

        <p className="brikko-prose mt-5">
          У стратегии <CodeInline>auto:ru-legal</CodeInline> поведение
          принципиально иное: если ВСЕ модели в цепочке (Yandex, Sber)
          недоступны, router возвращает HTTP 503, а не уходит на OpenAI. Это
          намеренно — лучше отдать клиенту ошибку, чем тихо нарушить
          152-ФЗ-периметр.
        </p>
      </section>

      <section id="usage" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">5. Как использовать</h2>
        <p className="brikko-prose mt-4">
          Smart Router включается одной заменой в поле <CodeInline>model</CodeInline>{' '}
          OpenAI-совместимого запроса. Никаких отдельных endpoints, никаких новых
          SDK — работает с любым клиентом, который умеет в Chat Completions.
        </p>

        <div className="mt-6 space-y-6">
          <CodeBlock
            label="Python (openai SDK)"
            code={`from openai import OpenAI

client = OpenAI(
    api_key="brk_live_...",
    base_url="https://api.brikko.ru/v1",
)

response = client.chat.completions.create(
    model="auto:cheap",  # router сам выберет модель
    messages=[
        {"role": "system", "content": "Классифицируй лид: hot/warm/cold."},
        {"role": "user", "content": "Нужна интеграция, бюджет 300к, сроки месяц."},
    ],
    temperature=0,
)

# Какую модель в итоге выбрал router — в заголовке ответа.
# Доступно через response._raw_response.headers["x-router-decision"].
print(response.choices[0].message.content)`}
          />

          <CodeBlock
            label="curl"
            code={`curl -sS https://api.brikko.ru/v1/chat/completions \\
  -H "Authorization: Bearer brk_live_..." \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto:cheap",
    "messages": [
      {"role": "user", "content": "Привет, как дела?"}
    ]
  }' \\
  -i  # печатает заголовки → видим x-router-decision`}
          />

          <CodeBlock
            label="TypeScript (openai-node)"
            code={`import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: process.env.BRIKKO_API_KEY,
  baseURL: 'https://api.brikko.ru/v1',
});

const completion = await client.chat.completions.create({
  model: 'auto:cheap',
  messages: [{ role: 'user', content: 'Привет!' }],
});

// Заголовки — через .withResponse() (см. openai-node README).
const { data, response } = await client.chat.completions
  .create({ model: 'auto:cheap', messages: [/* ... */] })
  .withResponse();

console.log(response.headers.get('x-router-decision'));`}
          />
        </div>

        <p className="brikko-prose mt-5">
          Готовые рецепты с разными стратегиями — в{' '}
          <Link href={'/docs/cookbook' as Route} className="brikko-link">
            cookbook
          </Link>
          . Полный каталог моделей, среди которых router выбирает, — на странице{' '}
          <Link href={'/models' as Route} className="brikko-link">
            /models
          </Link>
          . Цены — на{' '}
          <Link href={'/pricing' as Route} className="brikko-link">
            /pricing
          </Link>{' '}
          (открытый прайс в рублях по каждой модели).
        </p>
      </section>

      <section id="headers" className="mt-14 scroll-mt-20">
        <h2 className="brikko-h2">6. Заголовки ответа</h2>
        <p className="brikko-prose mt-4">
          На каждый ответ через router мы добавляем три служебных заголовка. Они
          нужны, чтобы вы могли логировать какую модель реально использовали (для
          биллинга, evals, дебага), и не удивляться, если выходные токены вдруг
          подешевели вдвое — значит, fallback сработал.
        </p>

        <dl className="mt-6 space-y-4">
          <HeaderEntry
            name="x-router-decision"
            description='ID модели, которая в итоге обработала запрос. Например, "claude-haiku-4-5". Если был fallback — это ID РЕЗЕРВНОЙ модели, не primary.'
          />
          <HeaderEntry
            name="x-router-strategy"
            description='Стратегия, по которой выбирали: "cheap" / "smart" / "fast" / "ru-legal". Полезно при логировании — если вы пробуете несколько стратегий A/B-тестом, в логах сразу видна метка.'
          />
          <HeaderEntry
            name="x-router-fallback-used"
            description='"true" если primary упал и router ушёл в резерв; "false" в норме. На дашборде /app/usage в Pro+ вы увидите процент fallback-запросов за неделю — спайки показывают сбой провайдера.'
          />
        </dl>

        <p className="brikko-prose mt-6">
          Эти заголовки доступны во всех SDK через стандартный response-object
          (см. примеры выше). Если вы используете streaming (
          <CodeInline>stream: true</CodeInline>), заголовки приходят на самый
          первый чанк ответа — то есть до того, как закончится генерация.
        </p>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс, карта не нужна. Smart
          Router работает на всех тарифах, включая welcome-бонус.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}

function ClassifierCard({
  label,
  heuristic,
  why,
}: {
  label: string;
  heuristic: React.ReactNode;
  why: string;
}) {
  return (
    <div className="brikko-card-flat">
      <div className="flex items-baseline gap-3">
        <span className="brikko-pill font-mono">{label}</span>
      </div>
      <p className="mt-3 text-body text-fg-muted">
        <span className="font-semibold text-fg-primary">Эвристика. </span>
        {heuristic}
      </p>
      <p className="mt-2 text-body-sm text-fg-muted">
        <span className="font-semibold text-fg-primary">Почему так. </span>
        {why}
      </p>
    </div>
  );
}

function CodeInline({ children }: { children: React.ReactNode }) {
  return <code className="brikko-code-inline">{children}</code>;
}

function CodeBlock({ label, code }: { label: string; code: string }) {
  return (
    <div>
      <p className="mb-2 text-body-sm font-medium text-fg-muted">{label}</p>
      <div className="brikko-code-shell">
        <pre className="brikko-code-pre">
          <code className="font-mono">{code}</code>
        </pre>
      </div>
    </div>
  );
}

function HeaderEntry({ name, description }: { name: string; description: string }) {
  return (
    <div className="brikko-card-flat">
      <dt className="font-mono text-body-sm font-semibold text-fg-primary">
        {name}
      </dt>
      <dd className="mt-1 text-body-sm text-fg-muted">{description}</dd>
    </div>
  );
}
