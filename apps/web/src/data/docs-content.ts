/**
 * Контент для docs.brikko.ru.
 * 12 разделов: Quickstart + Authentication + Migration + Models + Streaming
 *  + Tool calling + Errors + Rate limits + PII masking + Smart routing
 *  + Prompt caching + JSON Schema strict.
 *
 * Tone: технический, как Stripe / OpenAI docs. Без рекламы.
 * Code samples — на TS, Python, cURL, рабочие.
 */

export interface DocsCodeSample {
  language: 'typescript' | 'python' | 'bash' | 'json' | 'env';
  label: string;
  code: string;
}

export interface DocsSection {
  slug: string;
  title: string;
  group: 'getting-started' | 'api-reference' | 'features' | 'migration';
  body: string;
  code_samples?: DocsCodeSample[];
  related?: string[];
}

export const DOCS: Record<string, DocsSection> = {
  quickstart: {
    slug: 'quickstart',
    title: 'Quickstart — первый запрос за 5 минут',
    group: 'getting-started',
    body: `Brikko — OpenAI-совместимый API ко всем популярным LLM с одного ключа: OpenAI, Anthropic, Google, DeepSeek, Yandex, Sber. Если у вас уже есть код, работающий с OpenAI SDK, переход занимает 5 минут — нужно поменять две вещи: \`base_url\` и \`api_key\`.

## Шаг 1. Регистрация

Зарегистрируйтесь на [brikko.ru](https://brikko.ru). После подтверждения email на счёт начислится Welcome-бонус 200 ₽ — этого хватит примерно на 1000 запросов к \`gpt-5-mini\` для теста интеграции. Карту привязывать на этом этапе не надо.

## Шаг 2. Создание API-ключа

Откройте дашборд → раздел **API Keys** → **Create new key**. Дайте ключу описательное имя (например, \`local-dev\` или \`production-backend\`) — это поможет потом фильтровать запросы в \`/usage\`.

Ключ начинается с префикса \`sk-brk-\` и показывается ровно один раз. Скопируйте и сохраните в менеджер секретов или \`.env\`. Если потеряете — выпустите новый, восстановить нельзя.

## Шаг 3. Первый запрос

Endpoint: \`https://api.brikko.ru/v1\`. Формат полностью совместим с OpenAI Chat Completions API.

Все примеры ниже — рабочие, скопируйте и подставьте ваш ключ.

## Шаг 4. Что дальше

- Каталог из 38 моделей с ценами в ₽ — на странице \`/models\`.
- Если у вас код на ChatAnthropic SDK — Brikko поддерживает нативный \`/v1/messages\` (см. раздел Authentication).
- Для миграции с ProxyAPI / VseGPT / GPTunnel — раздел "Migration".
- Если работаете с ПДн (банк, страховая, госкорп) — обязательно прочитайте раздел "PII-маскинг".`,
    code_samples: [
      {
        language: 'bash',
        label: 'cURL',
        code: `curl https://api.brikko.ru/v1/chat/completions \\
  -H "Authorization: Bearer sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-5-mini",
    "messages": [
      {"role": "user", "content": "Привет! Расскажи в одно предложение о RAG."}
    ]
  }'`,
      },
      {
        language: 'python',
        label: 'Python (OpenAI SDK)',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    base_url="https://api.brikko.ru/v1",
)

response = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[
        {"role": "user", "content": "Привет! Расскажи в одно предложение о RAG."}
    ],
)
print(response.choices[0].message.content)`,
      },
      {
        language: 'typescript',
        label: 'TypeScript',
        code: `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  baseURL: "https://api.brikko.ru/v1",
});

const response = await client.chat.completions.create({
  model: "gpt-5-mini",
  messages: [
    { role: "user", content: "Привет! Расскажи в одно предложение о RAG." },
  ],
});

console.log(response.choices[0].message.content);`,
      },
    ],
    related: ['authentication', 'models', 'errors'],
  },

  authentication: {
    slug: 'authentication',
    title: 'Authentication',
    group: 'getting-started',
    body: `Все запросы авторизуются через Bearer-токен в заголовке \`Authorization\`. Никаких query-параметров для ключа, никаких cookies, никаких подписей запроса.

\`\`\`
Authorization: Bearer sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
\`\`\`

## Префикс sk-brk-

Все ключи Brikko начинаются с \`sk-brk-\`. Это позволяет менеджерам секретов и SAST-сканерам отличать наш ключ от ключей OpenAI (\`sk-\`), Anthropic (\`sk-ant-\`), DeepSeek и т.д.

## Anthropic Messages API

Для нативного формата Anthropic (\`/v1/messages\`) тот же ключ передаётся в заголовке \`x-api-key\`:

\`\`\`
x-api-key: sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
anthropic-version: 2023-06-01
\`\`\`

Это позволяет использовать SDK \`anthropic\` без изменений — нужно только указать \`base_url=https://api.brikko.ru\` (без \`/v1\` на конце, SDK добавит сам).

## Безопасность

- Никогда не коммитьте ключ в Git — добавьте \`.env\` в \`.gitignore\` и используйте \`dotenv\` или менеджер секретов.
- Для разных окружений (dev / staging / production) выпускайте отдельные ключи. В дашборде Brikko можно выставить дневной лимит на ключ — это страховка от утечки.
- Если ключ скомпрометирован — отзовите его в дашборде (\`API Keys → Revoke\`). После revoke все активные запросы с этим ключом получат 401 в течение секунд.`,
    related: ['quickstart', 'rate-limits'],
  },

  'migration-from-proxyapi': {
    slug: 'migration-from-proxyapi',
    title: 'Migration from ProxyAPI / VseGPT / GPTunnel',
    group: 'migration',
    body: `Если ваш код уже работает с ProxyAPI, VseGPT или GPTunnel — миграция на Brikko занимает одну строку: смена \`base_url\` и подстановка нашего ключа. Все эти сервисы используют OpenAI-совместимый формат, как и Brikko.

## Что меняется

| Параметр | Было | Стало |
|---|---|---|
| \`base_url\` | https://api.proxyapi.ru/openai/v1 (или аналог) | https://api.brikko.ru/v1 |
| \`api_key\` | sk-... (выданный прежним сервисом) | sk-brk-... (выданный в дашборде Brikko) |
| Имена моделей | gpt-4-turbo, claude-3-opus, ... | См. таблицу маппинга ниже |

## Что НЕ меняется

- Структура запроса (messages, role, content) — идентична.
- Streaming через SSE — формат \`data: {json}\\n\\n\`, событие \`[DONE]\`.
- Function calling, tool choice, response_format — тот же синтаксис.
- Embedding API (\`/v1/embeddings\`) — тот же формат запроса и ответа.

## Маппинг моделей (упрощённый)

Имена моделей в Brikko соответствуют официальным именам у провайдеров без обёртки и без префикса. Например:

| Прежнее (примерно) | Brikko |
|---|---|
| openai/gpt-4-turbo | gpt-5 (актуальный flagship) или gpt-5.4 |
| openai/gpt-4o-mini | gpt-5-mini |
| anthropic/claude-3-opus | claude-opus-4-7 (актуальный) |
| anthropic/claude-3.5-sonnet | claude-sonnet-4-6 |
| google/gemini-pro | gemini-3.1-pro |
| deepseek/deepseek-chat | deepseek-v3.2 |

Полный список — на [/models](https://brikko.ru/models). Если в вашем коде модель указана в формате с префиксом провайдера (\`openai/...\`, \`anthropic/...\`) — переименуйте на короткое имя без префикса.

## Что делать с биллингом

Brikko — отдельная платформа, отдельные кошельки. Остаток на прежнем сервисе автоматически не переносится. На время миграции рекомендуем держать оба ключа: на Brikko запустить часть трафика, проверить что всё работает, потом перенести 100% и закрыть прежний аккаунт.

## Юр. документы

Brikko работает в формате самозанятого (НПД). Единственный финансовый документ — чек НПД, формируется автоматически после каждого пополнения через ЮKassa. Чек НПД — допустимая первичка для УСН (Письмо ФНС от 16.09.2021 № АБ-4-20/13183). Актов, УПД, ЭДО, НДС-вычета — нет. Если они вам нужны — напишите на support@brikko.ru. Подробнее — на [/legal/info](https://brikko.ru/legal/info).`,
    code_samples: [
      {
        language: 'python',
        label: 'Было (Python)',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="sk-old-...",
    base_url="https://api.proxyapi.ru/openai/v1",
)

response = client.chat.completions.create(
    model="gpt-4-turbo",
    messages=[{"role": "user", "content": "ping"}],
)`,
      },
      {
        language: 'python',
        label: 'Стало (Python)',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    base_url="https://api.brikko.ru/v1",
)

response = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "ping"}],
)`,
      },
    ],
    related: ['quickstart', 'authentication', 'models'],
  },

  models: {
    slug: 'models',
    title: 'Модели',
    group: 'api-reference',
    body: `Brikko предоставляет 38 моделей от 6 провайдеров (OpenAI, Anthropic, Google, DeepSeek, Yandex, Sber). Каталог с ценами в рублях, контекстом, тегами модальностей — на публичной странице [/models](https://brikko.ru/models). Список доступен и через API без авторизации:

\`\`\`bash
curl https://api.brikko.ru/v1/models/public
\`\`\`

## Принцип отбора моделей

Мы курируем каталог: добавляем модель только когда у нас есть прямой контракт с провайдером (или с авторизованным реселлером в РФ для российских моделей). Это гарантирует что цена в каталоге — реальная цена, без посредников между нашим API и API провайдера. Имена моделей соответствуют официальным именам у вендора.

## Группы моделей по сценариям

- **Reasoning (сложные задачи):** gpt-5, gpt-5.4, o3, claude-opus-4-7
- **Балансные (по умолчанию):** gpt-5-mini, claude-sonnet-4-6, gemini-3.1-pro
- **Быстрые и дешёвые:** o4-mini, claude-haiku-4-5, gemini-3-flash, gpt-5.4-mini, deepseek-v3.2
- **Российские (ПДн в РФ):** yandexgpt-5.1-pro, yandexgpt-5-lite, gigachat-2-pro, gigachat-2-lite

## Smart routing presets

Вместо имени конкретной модели можно указать preset: \`auto:cheap\`, \`auto:smart\`, \`auto:fast\`, \`auto:ru-legal\`. Подробнее — в разделе "Smart routing".

## Как добавляются новые модели

Новые модели появляются в каталоге обычно в течение 1-3 дней после релиза у провайдера. Подписаться на обновления каталога — [t.me/brikko_models](https://t.me/brikko_models) (автопубликация при добавлении).`,
    related: ['quickstart', 'smart-routing'],
  },

  streaming: {
    slug: 'streaming',
    title: 'Streaming',
    group: 'api-reference',
    body: `Чтобы получать ответ модели по мере генерации (а не одним блоком в конце) — передайте \`stream: true\` в теле запроса. Brikko вернёт ответ в формате Server-Sent Events (SSE) — полностью совместимом с OpenAI streaming.

## Формат ответа

Каждое событие — строка вида \`data: {json}\\n\\n\`. Финальное событие — \`data: [DONE]\\n\\n\`. Между событиями возможны heartbeat-комментарии (\`: ping\\n\\n\`) — они нужны для обхода прокси с idle timeout, игнорируйте их в обработке.

## Anthropic streaming

Для нативного Anthropic API через \`/v1/messages\` Brikko возвращает события в формате Anthropic: \`message_start\`, \`content_block_start\`, \`content_block_delta\`, \`content_block_stop\`, \`message_delta\`, \`message_stop\`. SDK \`anthropic\` обрабатывает это из коробки.

## Усечение длинных ответов

Если клиент закрыл соединение во время streaming — Brikko прерывает запрос у провайдера и списывает только сгенерированные токены. Никаких лишних списаний.`,
    code_samples: [
      {
        language: 'python',
        label: 'Streaming в Python',
        code: `from openai import OpenAI

client = OpenAI(
    api_key="sk-brk-...",
    base_url="https://api.brikko.ru/v1",
)

stream = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Напиши хайку про код"}],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)`,
      },
    ],
    related: ['quickstart', 'errors'],
  },

  'tool-calling': {
    slug: 'tool-calling',
    title: 'Tool calling (Function calling)',
    group: 'features',
    body: `Tool calling позволяет модели "вызвать" функции, которые вы определили — например, поиск в БД, получение погоды, отправку email. Модель не выполняет код сама, она возвращает структурированный JSON с именем функции и аргументами, а ваш код решает, что с этим делать.

## Совместимость

- **OpenAI-формат (\`/v1/chat/completions\` с полем \`tools\`):** работает на всех 6 провайдерах — gpt-5*, o3, o4-mini, claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5, gemini-3.x, deepseek-v4-flash, deepseek-v4-pro, yandexgpt-5.1-pro, yandexgpt-5-lite, gigachat-2-pro, gigachat-2-lite.
- **Anthropic native (\`/v1/messages\` с полем \`tools\`):** работает на всех claude-*.
- **Тег "Tools"** в /models показывает поддержку — надёжный индикатор.

## Strict mode

Передайте \`"strict": true\` в описании tool — модель гарантированно вернёт JSON, валидный по вашей JSON Schema. Поддерживается на gpt-5*, claude-opus-4-7, claude-sonnet-4-6, **yandexgpt-5.1-pro** (Phase 5 #2 — 2026-05-09).

## Параллельные вызовы

- **GPT-5, Claude 4.6+, Gemini 3.x, DeepSeek V4, YandexGPT 5 Pro** — несколько tools параллельно в одном ответе (\`tool_calls\` — массив из нескольких элементов). Обрабатывайте их независимо.
- **GigaChat 2 (Pro/Lite/Max)** — **только 1 tool call за запрос** (hard cap upstream). Если модель захотела вызвать 2+ функции, она вернёт первую; для последующих сделайте отдельный turn в диалоге.

## Особенности RU-моделей

YandexGPT и GigaChat имеют свои внутренние форматы tools (Yandex использует \`toolCallList\`, GigaChat — legacy \`function_call\`). **Brikko прозрачно конвертирует их в OpenAI-формат** — ваш код пишется как для OpenAI, gateway адаптирует upstream-формат автоматически. Никаких provider-specific ветвлений в клиентском коде не нужно.

## Роутинг с tools

Если вы используете \`auto:smart\` preset — Brikko гарантирует выбор только из моделей с поддержкой tools. \`auto:cheap\` — может выбрать модель без tools, не используйте этот preset для tool calling сценариев.`,
    code_samples: [
      {
        language: 'python',
        label: 'Tool calling',
        code: `from openai import OpenAI

client = OpenAI(api_key="sk-brk-...", base_url="https://api.brikko.ru/v1")

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Узнать погоду в городе",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
            },
            "required": ["city"],
        },
        "strict": True,
    },
}]

response = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Какая погода в Москве?"}],
    tools=tools,
)

tool_call = response.choices[0].message.tool_calls[0]
print(tool_call.function.name, tool_call.function.arguments)
# get_weather {"city":"Москва"}`,
      },
    ],
    related: ['json-schema-strict', 'models'],
  },

  errors: {
    slug: 'errors',
    title: 'Ошибки',
    group: 'api-reference',
    body: `Brikko возвращает HTTP-коды совместимые с OpenAI API. Тело ошибки — JSON с полями \`error.code\`, \`error.message\`, \`error.type\`.

## 400 Bad Request

Невалидное тело запроса. Чаще всего — несуществующая модель, неправильный формат messages, превышение max_tokens модели. \`error.message\` содержит конкретную причину. Не ретраить.

## 401 Unauthorized

API-ключ невалиден, отозван, истёк, либо отсутствует заголовок \`Authorization\`. Проверьте префикс \`sk-brk-\`. Если ключ только что выпущен — подождите ~5 секунд (распространение по edge-нодам). Не ретраить.

## 402 Payment Required

Закончился баланс на счёте либо превышен лимит ключа (daily/monthly budget). Пополните счёт или поднимите лимит ключа в дашборде. Не ретраить автоматически — сначала уведомьте администратора.

## 429 Too Many Requests

Превышен rate limit — либо тарифный лимит Brikko (запросов в минуту по тарифу), либо rate limit провайдера на конкретную модель. Заголовок \`Retry-After\` указывает секунды до ретрая. Если включён smart routing — Brikko уже сам перенаправил запрос на резервную модель, 429 для вас не возникнет.

## 500 Internal Server Error

Внутренняя ошибка Brikko. Ретраить с backoff — обычно проходит со второй попытки. Если повторяется — пишите на support@brikko.ru с \`X-Request-ID\` из заголовка ответа.

## 503 Service Unavailable

Все провайдеры этой модели недоступны (ситуация редкая — обычно failover спасает). Используйте preset \`auto:smart\` вместо конкретной модели — он переключится на ближайший аналог.

## X-Request-ID

В каждом ответе (включая ошибки) — заголовок \`X-Request-ID\`. Сохраняйте его в логах: при обращении в поддержку с этим ID мы найдём конкретный запрос за секунду.`,
    related: ['rate-limits', 'smart-routing'],
  },

  'rate-limits': {
    slug: 'rate-limits',
    title: 'Rate limits',
    group: 'api-reference',
    body: `Rate limits Brikko зависят от тарифа. Лимиты применяются на уровне аккаунта (общие на все ключи) — если у вас много ключей, RPM делится между ними.

| Тариф | RPM (запросов/минуту) | TPM (токенов/минуту) |
|---|---|---|
| Pay-as-you-go | 60 | 60 000 |
| Pro | 500 | 500 000 |
| Pro Privacy | 500 | 500 000 |
| Team | 3000 | 3 000 000 |

## Заголовки rate limit

В каждом ответе:
- \`X-RateLimit-Limit-Requests\` — лимит RPM
- \`X-RateLimit-Remaining-Requests\` — сколько осталось в текущей минуте
- \`X-RateLimit-Reset-Requests\` — секунды до сброса
- Аналогичные \`*-Tokens\` для TPM

## Что делать при 429

1. Если у вас включён smart routing — Brikko сам ретраит на резервный канал, 429 вы видеть не должны.
2. Если 429 возник — соблюдайте \`Retry-After\` (заголовок в секундах).
3. Для предотвращения — используйте exponential backoff в SDK (OpenAI Python SDK делает это автоматически).
4. Если лимит постоянно режет — пишите на support, рассмотрим повышение в рамках текущего тарифа.`,
    related: ['errors', 'smart-routing'],
  },

  'pii-masking': {
    slug: 'pii-masking',
    title: 'PII-маскинг — соответствие 152-ФЗ',
    group: 'features',
    body: `Brikko встроенно маскирует персональные данные в промптах перед отправкой в OpenAI / Anthropic / Google. Это закрывает 152-ФЗ для российской компании, работающей с ПДн (банк, страховая, медицина, госкорпорация).

## Зачем

По 152-ФЗ передача персональных данных третьему лицу за пределы РФ требует согласия субъекта. Если ваше приложение шлёт в LLM промпт вида "обработай заявку Иванова Ивана с паспортом 4500 123456" — это передача ПДн в OpenAI/Anthropic в США без правовых оснований. Штрафы по ст. 13.11 КоАП — до 18 млн ₽.

PII-маскинг решает это технически: до отправки запроса в провайдера Brikko находит ПДн в промпте, заменяет их на токены вида \`<PERSON_1>\`, \`<PASSPORT_1>\`, отправляет замаскированный текст в LLM. Ответ модели возвращается клиенту с обратной подстановкой реальных значений.

## Что маскируется

| Категория | Пример | Токен |
|---|---|---|
| ФИО | Иванов Иван Петрович | \`<PERSON_1>\` |
| Телефон | +7 905 123-45-67 | \`<PHONE_1>\` |
| Email | ivan@example.com | \`<EMAIL_1>\` |
| Паспорт РФ | 4500 123456 | \`<PASSPORT_1>\` |
| ИНН | 7707083893 | \`<INN_1>\` |
| СНИЛС | 112-233-445 95 | \`<SNILS_1>\` |
| Банковская карта | 4276 1234 5678 9012 | \`<CARD_1>\` |
| Адрес РФ | г. Москва, ул. Тверская 1 | \`<ADDRESS_1>\` |
| Дата рождения в формате ДД.ММ.ГГГГ | 15.03.1990 | \`<DOB_1>\` |

Совпадение токенов сохраняется в рамках одного запроса: один и тот же Иванов получит \`<PERSON_1>\` в каждом упоминании, не разные номера. Это позволяет модели понимать связи в тексте.

## Как включить

В дашборде: Settings → Privacy → "Включить PII-маскинг". После включения — действует на все запросы аккаунта. Можно включить per-key через API:

\`\`\`bash
PATCH /v1/keys/{key_id}
Content-Type: application/json
{"pii_masking": "strict"}
\`\`\`

Режимы: \`off\` (по умолчанию), \`strict\` (всё перечисленное выше), \`audit\` (не маскирует, но логирует найденное — для аудита перед production).

## Что НЕ маскируется

- **System prompt** — мы считаем его безопасным (это ваш собственный текст). Если вы кладёте ПДн в system — это сознательное решение.
- **Содержимое JSON Schema / tool definitions** — не маскируется, чтобы не сломать структуру.
- **Имена брендов и публичных компаний** ("Сбербанк", "Apple") — это не ПДн.

## Юридические гарантии

PII-маскинг закреплён в публичной оферте (см. /legal/oferta) и распространяется на тариф Pro Privacy. Запросы к маскингу пишутся в audit log с retention 1 год — этого достаточно для типовых проверок Роскомнадзора. Brikko как самозанятый не выступает оператором ПДн ваших клиентов; вы остаётесь оператором, Brikko — техническим обработчиком на основании публичной оферты.

## Гарантия 99.9%

Мы публикуем бенчмарк маскировщика на 1000+ корпусных записей с реалистичными ПДн (см. /benchmarks/pii). Целевая метрика recall = 99.9% по 7 категориям выше. Если на ваших данных recall ниже — возвращаем стоимость подписки за месяц.

## Ограничения

- Никакая регэкс-эвристика не даст абсолютные 100% на любых данных — публикуемый recall измерен на нашем корпусе и обновляется ежемесячно. Для критических сценариев советуем дополнительный preprocessing на стороне клиента.
- Маскинг добавляет 30-80 ms к латентности запроса.
- Для streaming-ответов обратная подстановка работает буферированием по абзацам — латентность первого токена немного выше.`,
    related: ['authentication', 'errors'],
  },

  'smart-routing': {
    slug: 'smart-routing',
    title: 'Smart routing и failover',
    group: 'features',
    body: `Smart routing — это выбор оптимальной модели под каждый запрос автоматически, без указания конкретной модели в коде. Failover — переключение на резервный канал при сбое или 429 от провайдера.

## Как использовать

Вместо имени модели передайте preset:

| Preset | Что делает |
|---|---|
| \`auto:cheap\` | Самая дешёвая модель, способная решить задачу. Приоритет — стоимость. |
| \`auto:smart\` | Балансное качество/цена. Подходит для большинства production. |
| \`auto:fast\` | Минимальная латентность first-token. Для интерактивных интерфейсов. |
| \`auto:ru-legal\` | Только модели, размещённые в РФ (Yandex, Sber). Для строгих требований по 152-ФЗ. |

\`\`\`json
{
  "model": "auto:smart",
  "messages": [{"role": "user", "content": "..."}]
}
\`\`\`

В ответе придёт фактически использованная модель в поле \`model\` и в заголовке \`X-Brikko-Model\`. Это полезно для логов и отладки.

## Логика выбора в auto:smart

1. Анализ промпта (длина, наличие code, наличие tool calls, язык).
2. Выбор кандидата по таблице "тип запроса → подходящая модель" (актуальная таблица — в дашборде Settings → Routing → "Show preset rules").
3. Проверка доступности кандидата (текущая latency, не превышен ли rate limit).
4. Если кандидат недоступен — переход к следующему по приоритету.

## Failover

Failover работает и при использовании конкретной модели (не только presets). На любую неуспешную попытку (429, 5xx, таймаут >30 сек):

1. Brikko ждёт \`Retry-After\` (если есть) или 1 секунду.
2. Делает один retry на тот же провайдер.
3. Если снова неуспех — переходит на резервный канал той же модели (если у нас несколько контрактов на одну модель — например, Claude через Anthropic Direct и Bedrock).
4. Если все каналы недоступны — переходит на ближайшую по характеристикам модель (только в режиме preset).

Failover в среднем добавляет 1-3 секунды к запросу при сбое. Без сбоя — 0 ms overhead.

## Кастомные routing rules

Тариф Team — настройка собственных правил в дашборде. Например: "если в промпте есть слово 'translate' — всегда DeepSeek V4". Применяется поверх preset.

## Прозрачность

Каждый ответ содержит заголовок \`x-router-decision\` с именем выбранной модели и причиной выбора. В дашборде — агрегированная статистика «какой % трафика через какого провайдера» за любой период.`,
    related: ['models', 'rate-limits', 'errors'],
  },

  'prompt-caching': {
    slug: 'prompt-caching',
    title: 'Prompt caching',
    group: 'features',
    body: `Prompt caching позволяет провайдеру переиспользовать вычисления для повторяющихся частей промпта (system prompt, история разговора, длинный документ). Экономия — до 90% на input-токенах кэшированной части и снижение latency на 30-50%.

## Кто поддерживает

| Провайдер | Тип | Tokens минимум |
|---|---|---|
| Anthropic (Claude 4.x) | manual (cache_control) | 1024 |
| OpenAI (GPT-5, GPT-5.4, o3, o4) | автоматический (no setup) | 1024 |
| Google (Gemini 3.x) | manual (cached_content) | 32 768 |
| DeepSeek V3.2 | автоматический | 1024 |

Brikko прокидывает кэш-флаги 1:1 в провайдер — мы не добавляем своего слоя кэширования (это создало бы дополнительную задержку и точку отказа).

## Anthropic — пример

Помечаете блок промпта как кэшируемый — следующие запросы с тем же блоком используют кэш и стоят ~10% от обычной цены этой части.

\`\`\`python
response = client.messages.create(
    model="claude-sonnet-4-6",
    system=[
        {
            "type": "text",
            "text": "<длинная инструкция или документ>",
            "cache_control": {"type": "ephemeral"}
        }
    ],
    messages=[{"role": "user", "content": "вопрос"}],
)
\`\`\`

## OpenAI — автоматически

OpenAI кэширует префиксы запросов автоматически с релиза GPT-4o. Никаких флагов передавать не надо — если первые 1024 токена вашего промпта совпадают с предыдущим запросом, входная стоимость этой части уменьшается на 50%.

## Что показывает Brikko

В дашборде \`/usage\` для каждого запроса видно:
- \`input_tokens_cached\` — сколько input-токенов попало в кэш
- \`input_tokens_uncached\` — сколько посчиталось как новые
- Реальная стоимость с учётом кэша — в \`cost_rub\`

## Tier-gate

Prompt caching доступен на всех тарифах от Pro Features и выше. На Pay-as-you-go — флаги в запросах работают, но скидка на кэш не применяется (упрощённый биллинг).`,
    related: ['smart-routing', 'tool-calling'],
  },

  'json-schema-strict': {
    slug: 'json-schema-strict',
    title: 'Structured outputs (JSON Schema strict)',
    group: 'features',
    body: `Strict JSON Schema гарантирует что модель вернёт ответ, валидный по вашей схеме — без галлюцинаций полей и неправильных типов. Это упрощает интеграцию с типизированными бэкендами.

## Когда использовать

- Извлечение структурированных данных из текста (классификация, NER, парсинг резюме).
- Tool calling, где схема функции сложная.
- Генерация ответов для дальнейшей записи в БД с типизацией.

## Совместимость

Strict mode поддерживается на:
- OpenAI: gpt-5, gpt-5-mini, gpt-5.4, gpt-5.4-mini, o3, o4-mini.
- Anthropic: claude-opus-4-7, claude-sonnet-4-6.

На остальных моделях — schema передаётся как hint, валидность не гарантируется (модель скорее всего вернёт корректный JSON, но без формальной гарантии). На странице /models у моделей есть тег "Strict JSON" — фильтруйте по нему.

## Использование

\`\`\`python
response = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Извлеки данные из: 'Иван, 35 лет, разработчик'"}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "person_extraction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                    "profession": {"type": "string"},
                },
                "required": ["name", "age", "profession"],
                "additionalProperties": False,
            },
        },
    },
)
import json
data = json.loads(response.choices[0].message.content)
\`\`\`

## Ограничения strict mode

Документация OpenAI описывает ограничения JSON Schema в strict mode — Brikko применяет те же правила (мы не модифицируем схему):

- \`additionalProperties: false\` обязательно на всех объектах.
- Все поля обязательны (required) — для опциональных используйте union с null.
- Не поддерживаются: \`oneOf\`, \`anyOf\` (используйте \`enum\` вместо них), \`if/then/else\`, \`$ref\` к внешним схемам.
- Глубина вложенности — до 5 уровней.
- Размер схемы — до 100 KB после JSON.stringify.`,
    related: ['tool-calling', 'models'],
  },
};

export const DOCS_LIST: DocsSection[] = Object.values(DOCS);

export const DOCS_GROUPS = [
  { id: 'getting-started', title: 'Начало работы' },
  { id: 'api-reference', title: 'API Reference' },
  { id: 'features', title: 'Возможности' },
  { id: 'migration', title: 'Миграция' },
] as const;
