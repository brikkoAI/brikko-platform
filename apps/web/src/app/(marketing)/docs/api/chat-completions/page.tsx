import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';
import { ApiTable } from '@/components/docs/ApiTable';

export const metadata: Metadata = {
  title: 'POST /v1/chat/completions — API Reference · Brikko',
  description:
    'Полный reference OpenAI-совместимого endpoint /v1/chat/completions: поля запроса, streaming, function calling, PII-protect, error codes, примеры на Python / Node / curl.',
  alternates: { canonical: '/docs/api/chat-completions' },
};

/**
 * /docs/api/chat-completions — UX-обоснование структуры:
 *
 *   1) Hero — endpoint signature (POST + path) большим mono. Это первое, что
 *      ищет глазами разработчик. Линки на родственные endpoints в правом
 *      верхнем углу — чтобы не возвращаться в hub.
 *   2) §1 Request — таблица параметров ПЕРВЫМ контентным блоком (не код).
 *      Это reference, не tutorial: пользователь пришёл узнать «можно ли мне
 *      передать X», и ответ — в таблице.
 *   3) §2 Response — shape JSON, минимум прозы.
 *   4) §3 Streaming — отдельная секция, потому что меняет shape ответа
 *      кардинально (chunks вместо single object).
 *   5) §4 PII protect — наш USP, отдельной секцией. Не закопано в Request
 *      table.
 *   6) §5 Errors — обязательная секция. Без неё разработчик не сможет
 *      обработать 402 (баланс закончился) или 429 (rate-limit) корректно.
 *   7) §6 Примеры — три языка, последняя секция. Пользователь, дочитавший
 *      до сюда, уже знает контракт; примеры — закрепление.
 *
 * ВНИМАНИЕ: поля в ApiTable взяты из реального ChatCompletionRequestBody
 * в apps/gateway/voltari_gateway/api/chat.py. НЕ выдумано.
 */

const REQUEST_ROWS = [
  {
    field: 'model',
    type: 'string',
    required: true,
    description: (
      <>
        ID модели или auto-стратегия (
        <code className="font-mono text-body-sm">auto:cheap</code> /{' '}
        <code className="font-mono text-body-sm">auto:smart</code> /{' '}
        <code className="font-mono text-body-sm">auto:fast</code> /{' '}
        <code className="font-mono text-body-sm">auto:ru-legal</code>). Полный
        каталог —{' '}
        <Link href={'/models' as Route} className="brikko-link">
          /models
        </Link>
        .
      </>
    ),
  },
  {
    field: 'messages',
    type: 'array',
    required: true,
    description: (
      <>
        Массив сообщений диалога. Каждое — объект{' '}
        <code className="font-mono text-body-sm">
          {'{role, content, name?, tool_call_id?, tool_calls?}'}
        </code>
        . <code className="font-mono text-body-sm">role</code> ∈{' '}
        <code className="font-mono text-body-sm">
          system | user | assistant | tool | developer
        </code>
        . Минимум 1 элемент. Текст — до 200k символов на блок, до 400k суммарно.
      </>
    ),
  },
  {
    field: 'temperature',
    type: 'float',
    default: 'провайдер',
    description: 'От 0.0 до 2.0. Контролирует случайность. 0 = детерминированный.',
  },
  {
    field: 'top_p',
    type: 'float',
    default: 'провайдер',
    description: 'Nucleus sampling. От 0.0 до 1.0. Альтернатива temperature.',
  },
  {
    field: 'max_tokens',
    type: 'int',
    default: 'провайдер',
    description: 'Лимит токенов в ответе. От 1 до 131 072 (если модель столько умеет).',
  },
  {
    field: 'stream',
    type: 'bool',
    default: 'false',
    description: (
      <>
        Если <code className="font-mono text-body-sm">true</code> — Server-Sent
        Events со стандартными OpenAI-чанками. См. секцию Streaming ниже.
      </>
    ),
  },
  {
    field: 'stop',
    type: 'string | string[]',
    description: 'Строка или список stop-последовательностей. Стандарт OpenAI.',
  },
  {
    field: 'tools',
    type: 'array',
    description: (
      <>
        Function-calling в формате OpenAI tools-spec. Если хотя бы одна модель
        в стратегии не поддерживает tools — Smart Router её исключит.
      </>
    ),
  },
  {
    field: 'tool_choice',
    type: 'string | object',
    description: (
      <>
        <code className="font-mono text-body-sm">&quot;auto&quot;</code> /{' '}
        <code className="font-mono text-body-sm">&quot;none&quot;</code> /{' '}
        <code className="font-mono text-body-sm">&quot;required&quot;</code>{' '}
        или конкретный tool через объект.
      </>
    ),
  },
  {
    field: 'response_format',
    type: 'object',
    description: (
      <>
        <code className="font-mono text-body-sm">
          {'{type: "json_object"}'}
        </code>{' '}
        для JSON mode или{' '}
        <code className="font-mono text-body-sm">
          {'{type: "json_schema", json_schema: {...}}'}
        </code>{' '}
        для structured outputs.
      </>
    ),
  },
  {
    field: 'seed',
    type: 'int',
    description: 'Для воспроизводимости (если провайдер поддерживает).',
  },
  {
    field: 'reasoning_effort',
    type: 'string',
    description: (
      <>
        <code className="font-mono text-body-sm">low | medium | high</code> для
        reasoning-моделей (o-series, Claude extended thinking). Триггерит ветку
        классификатора <code className="font-mono text-body-sm">reasoning</code>{' '}
        в Smart Router.
      </>
    ),
  },
  {
    field: 'pii_protect',
    type: 'bool',
    description: (
      <>
        Brikko-расширение. <code className="font-mono text-body-sm">true</code>{' '}
        — gateway маскирует ПД в prompt&apos;е перед отправкой провайдеру и
        восстанавливает в ответе. Account-level флаг overrides этот параметр —
        если у вас глобально включено, передавать не нужно. Подробнее —{' '}
        <Link href={'/docs/concepts/privacy-v2' as Route} className="brikko-link">
          Privacy v2
        </Link>
        .
      </>
    ),
  },
  {
    field: 'failover',
    type: 'bool',
    default: 'true',
    description: (
      <>
        Brikko-расширение. <code className="font-mono text-body-sm">false</code>{' '}
        — отключить fallback-цепочку (если primary упал — вернуть 502, не
        пытаться резерв).
      </>
    ),
  },
  {
    field: 'exclude_providers',
    type: 'string[]',
    description: (
      <>
        Brikko-расширение. Список провайдеров, которых router исключит при
        выборе модели. Например,{' '}
        <code className="font-mono text-body-sm">[&quot;openai&quot;]</code> для
        compliance-сценариев.
      </>
    ),
  },
];

const RESPONSE_EXAMPLE = `{
  "id": "chatcmpl-9X7q...",
  "object": "chat.completion",
  "created": 1746540000,
  "model": "gpt-5.4-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Привет! Чем могу помочь?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 7,
    "total_tokens": 19
  }
}`;

const STREAM_EXAMPLE = `data: {"id":"chatcmpl-9X7q...","choices":[{"delta":{"role":"assistant","content":""}}]}

data: {"id":"chatcmpl-9X7q...","choices":[{"delta":{"content":"Привет"}}]}

data: {"id":"chatcmpl-9X7q...","choices":[{"delta":{"content":"!"}}]}

data: {"id":"chatcmpl-9X7q...","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]`;

const PYTHON_EXAMPLE = `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-...",
    base_url="https://api.brikko.ru/v1",
)

resp = client.chat.completions.create(
    model="auto:cheap",
    messages=[
        {"role": "system", "content": "Ты — ассистент службы поддержки."},
        {"role": "user", "content": "Когда придёт заказ #12345?"},
    ],
    temperature=0.3,
    max_tokens=300,
)

print(resp.choices[0].message.content)
print(resp.usage.total_tokens)`;

const NODE_EXAMPLE = `import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: process.env.BRIKKO_API_KEY,
  baseURL: 'https://api.brikko.ru/v1',
});

const resp = await client.chat.completions.create({
  model: 'auto:smart',
  messages: [
    { role: 'user', content: 'Объясни смарт-роутинг в Brikko' },
  ],
  stream: false,
});

console.log(resp.choices[0].message.content);`;

const CURL_EXAMPLE = `curl -sS https://api.brikko.ru/v1/chat/completions \\
  -H "Authorization: Bearer $BRIKKO_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [
      {"role": "user", "content": "Привет!"}
    ],
    "max_tokens": 100
  }' \\
  -i  # печатает заголовки → видим x-router-decision`;

const ERROR_ROWS = [
  {
    field: '400',
    type: 'invalid_request',
    description:
      'Невалидный body: отсутствует обязательное поле, превышен лимит длины, нераспознанный role. В detail — точная причина.',
  },
  {
    field: '401',
    type: 'invalid_api_key',
    description: 'Ключ не прислан / отозван / не существует. Проверьте Authorization header.',
  },
  {
    field: '402',
    type: 'insufficient_quota',
    description:
      'Баланс ниже estimated cost запроса (с буфером 1.5×). Пополните счёт в /app/billing или подключите авто-пополнение.',
  },
  {
    field: '404',
    type: 'model_not_found',
    description:
      'Модель не существует или недоступна вашему тарифу (например, premium-модели на Pay-as-you-go).',
  },
  {
    field: '413',
    type: 'payload_too_large',
    description: 'Body превышает лимиты (200k chars на сообщение, 400k суммарно).',
  },
  {
    field: '429',
    type: 'rate_limit',
    description:
      'Превышен RPM/TPM лимит вашего тарифа. Заголовок Retry-After указывает, через сколько секунд можно повторить.',
  },
  {
    field: '502',
    type: 'upstream_error',
    description: 'Провайдер вернул ошибку. Если включён failover — мы уже попробовали все резервы.',
  },
  {
    field: '503',
    type: 'service_unavailable',
    description: 'Все eligible-модели недоступны. Типично — массовый сбой нескольких провайдеров.',
  },
];

export default function ChatCompletionsDocPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          API Reference
        </p>
        <h1 className="brikko-h1 mt-3">Chat Completions</h1>
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <span className="brikko-pill font-mono">POST</span>
          <code className="font-mono text-body font-semibold text-fg-primary">
            https://api.brikko.ru/v1/chat/completions
          </code>
        </div>
        <p className="brikko-lede mt-6">
          OpenAI-совместимый endpoint для chat-completions. Полный контракт OpenAI
          Chat Completions: те же поля, тот же streaming, function calling, JSON
          mode. Большинство SDK работают сменой{' '}
          <code className="brikko-code-inline">base_url</code> на{' '}
          <code className="brikko-code-inline">https://api.brikko.ru/v1</code>.
        </p>
      </header>

      <section id="request" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Request</h2>
        <p className="brikko-prose mt-4">
          Тело — JSON. Все поля стандартного OpenAI Chat Completions поддерживаются{' '}
          1-в-1. Brikko-специфичные поля помечены отдельно — стандартные клиенты
          игнорируют их и работают как обычно.
        </p>
        <ApiTable rows={REQUEST_ROWS} />
        <p className="brikko-prose mt-5">
          Также передаются HTTP-заголовки:{' '}
          <code className="brikko-code-inline">Authorization: Bearer sk-brk-...</code>{' '}
          (обязательный),{' '}
          <code className="brikko-code-inline">X-PII-Protect: true</code>{' '}
          (альтернатива body-полю <code className="brikko-code-inline">pii_protect</code>),{' '}
          <code className="brikko-code-inline">X-Idempotency-Key</code>{' '}
          (опционально, для повторных попыток).
        </p>
      </section>

      <section id="response" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Response</h2>
        <p className="brikko-prose mt-4">
          Стандартный OpenAI-shape ответа. Поле{' '}
          <code className="brikko-code-inline">model</code> в ответе — реальная
          модель, которую выбрал router (если использовали{' '}
          <code className="brikko-code-inline">auto:*</code>).
        </p>
        <CodeBlock label="200 OK" code={RESPONSE_EXAMPLE} />
        <p className="brikko-prose mt-5">
          Заголовки ответа Brikko:{' '}
          <code className="brikko-code-inline">x-router-decision</code> (ID
          выбранной модели),{' '}
          <code className="brikko-code-inline">x-router-strategy</code>,{' '}
          <code className="brikko-code-inline">x-router-fallback-used</code>,{' '}
          <code className="brikko-code-inline">x-gateway-cost-kop</code>{' '}
          (стоимость в копейках),{' '}
          <code className="brikko-code-inline">x-request-id</code>. Подробнее —{' '}
          <Link href={'/docs/smart-routing' as Route} className="brikko-link">
            Smart Routing
          </Link>
          .
        </p>
      </section>

      <section id="streaming" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Streaming</h2>
        <p className="brikko-prose mt-4">
          Передайте <code className="brikko-code-inline">stream: true</code> —
          получите SSE-поток в стандартном OpenAI-формате. Каждый чанк — JSON в{' '}
          <code className="brikko-code-inline">data: ...</code>, последний чанк —{' '}
          <code className="brikko-code-inline">data: [DONE]</code>.
        </p>
        <CodeBlock label="SSE stream" code={STREAM_EXAMPLE} />
        <p className="brikko-prose mt-5">
          Заголовки router-а доступны на самом первом чанке — это позволяет
          логировать выбранную модель ещё до окончания генерации. Если клиент
          разорвёт соединение посреди стрима — Brikko корректно завершит
          upstream-запрос и спишет только реально полученные токены (см.{' '}
          <Link href={'/docs/concepts/pricing' as Route} className="brikko-link">
            биллинг
          </Link>
          ).
        </p>
        <p className="brikko-prose mt-4">
          Mid-stream cross-provider failover{' '}
          <strong className="font-semibold text-fg-primary">не поддерживается</strong>
          : если первый токен пришёл — router больше не переключится. Failover
          работает только до первого байта (8-секундный TTFT-таймаут).
        </p>
      </section>

      <section id="pii" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">PII Protect</h2>
        <p className="brikko-prose mt-4">
          Brikko-специфичная фича для 152-ФЗ. С{' '}
          <code className="brikko-code-inline">pii_protect: true</code> (или
          заголовком <code className="brikko-code-inline">X-PII-Protect: true</code>):
        </p>
        <ol className="mt-4 list-decimal space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            Gateway сканирует <code className="brikko-code-inline">messages</code>{' '}
            на ПД (ФИО, ИНН, СНИЛС, телефон, email, паспорт, банк-карты, IP).
          </li>
          <li>
            Заменяет ПД на плейсхолдеры{' '}
            <code className="brikko-code-inline">&lt;NAME_1&gt;</code>,{' '}
            <code className="brikko-code-inline">&lt;PHONE_1&gt;</code> и т.д.
          </li>
          <li>
            Отправляет санитизированный промпт провайдеру. Провайдер видит
            плейсхолдеры, не оригинальные значения.
          </li>
          <li>
            В ответе провайдера восстанавливает оригинальные значения по
            mapping&apos;у.
          </li>
        </ol>
        <p className="brikko-prose mt-5">
          Если нужно вмешательство в текст ВНЕ запроса к LLM (например,
          санитизировать перед собственным ML-pipeline) — используйте{' '}
          <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
            standalone /v1/anonymize
          </Link>
          . Концепция и сравнение с Presidio —{' '}
          <Link href={'/docs/concepts/privacy-v2' as Route} className="brikko-link">
            Privacy v2
          </Link>
          .
        </p>
      </section>

      <section id="errors" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Error codes</h2>
        <p className="brikko-prose mt-4">
          Все ошибки в формате OpenAI:{' '}
          <code className="brikko-code-inline">
            {'{"error": {"message": "...", "type": "...", "code": "..."}}'}
          </code>
          . Код ответа HTTP — в первом столбце таблицы.
        </p>
        <ApiTable rows={ERROR_ROWS} />
      </section>

      <section id="examples" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Примеры</h2>
        <p className="brikko-prose mt-4">
          Три варианта одного и того же запроса. Все используют стандартные SDK
          (никаких brikko-специфичных пакетов не требуется).
        </p>
        <div className="mt-6 space-y-6">
          <CodeBlock label="Python (openai SDK)" code={PYTHON_EXAMPLE} flush />
          <CodeBlock label="Node.js (openai SDK)" code={NODE_EXAMPLE} flush />
          <CodeBlock label="curl" code={CURL_EXAMPLE} flush />
        </div>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс, карта не нужна.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}
