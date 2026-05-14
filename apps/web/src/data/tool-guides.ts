/**
 * Контент для страниц /tools/[slug].
 * 6 интеграционных гайдов: Cursor, Claude Code, Cline, LangChain, n8n, OpenClaw.
 *
 * Tone: конкретный, как Stripe docs. Без рекламной шелухи.
 * Каждый шаг — рабочий код с реальными значениями.
 *
 * Юр.: после кейса 2026-04-30 не называем конкурентов нарушителями.
 */

export interface ToolGuideStep {
  title: string;
  body: string;
  code: string | null;
  language?: 'bash' | 'json' | 'typescript' | 'python' | 'yaml' | 'env';
}

export interface ToolGuideIssue {
  q: string;
  a: string;
}

export interface ToolGuide {
  slug: string;
  name: string;
  icon: string;
  tagline: string;
  description: string;
  why_brikko: string[];
  steps: ToolGuideStep[];
  common_issues: ToolGuideIssue[];
  related: string[];
  seo_title: string;
  seo_description: string;
}

export const TOOL_GUIDES = {
  cursor: {
    slug: 'cursor',
    name: 'Cursor IDE',
    icon: 'cursor',
    tagline: 'AI-редактор кода с GPT-5 и Claude Opus через Brikko API.',
    description:
      'Cursor — форк VS Code с встроенным AI-агентом: inline-completion, чат по проекту, agent-mode для редактирования нескольких файлов разом. По умолчанию ходит в OpenAI и Anthropic напрямую — российской картой это не оплатить, чек не получить, в промптах летят данные клиентов в США. Brikko подменяет endpoint одной строкой: используете тот же Cursor с теми же моделями, но платите в рублях через ЮKassa, получаете чек самозанятого после каждого пополнения, а PII-маскинг (ФИО, телефоны, паспорта) отрезается до отправки в провайдера. Для команды на тарифе Team — общий ключ, лимиты по разработчику, отчёт по расходам в админке.',
    why_brikko: [
      'Оплата в рублях через ЮKassa, чек НПД после каждого пополнения. Без иностранных карт и крипты.',
      'PII-маскинг до отправки в OpenAI/Anthropic. ФИО, телефоны, паспорта, ИНН, СНИЛС — заменяются на токены. 152-ФЗ закрыт.',
      'Smart routing: на 429 от OpenAI запрос автоматически уходит в резервную модель того же класса. Cursor не зависает.',
    ],
    steps: [
      {
        title: 'Шаг 1. Получить API-ключ Brikko',
        body: 'Зарегистрируйтесь на brikko.ru (Welcome-бонус 200 ₽ — хватит на ~1000 запросов к gpt-5-mini для теста). В дашборде → API Keys → Create new key. Ключ начинается с sk-brk-. Скопируйте, второй раз показан не будет.',
        code: null,
      },
      {
        title: 'Шаг 2. Открыть настройки моделей в Cursor',
        body: 'В Cursor: Cmd/Ctrl + Shift + P → "Cursor Settings" → вкладка Models. Внизу секции — "OpenAI API Key" и "Override OpenAI Base URL". Это всё что нужно поменять.',
        code: null,
      },
      {
        title: 'Шаг 3. Заменить baseURL и ключ',
        body: 'Включите toggle "Override OpenAI Base URL". Вставьте baseURL Brikko и API-ключ. После этого выберите в списке моделей те, что хотите использовать — Cursor пошлёт их по нашему OpenAI-совместимому endpoint.',
        code: 'OpenAI API Key:        sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nOverride Base URL:      https://api.brikko.ru/v1\n\n# Включите модели в списке Models:\n#   gpt-5, gpt-5-mini, gpt-5.4, o3, o4-mini\n#   claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5',
        language: 'env',
      },
      {
        title: 'Шаг 4. Проверить через Composer',
        body: 'Откройте любой файл, нажмите Cmd/Ctrl + L (чат), задайте вопрос. Если ответ пришёл — всё работает. Если 401 — проверьте ключ. Если 404 model not found — выберите другую модель в меню Composer (не все модели Cursor совпадают с нашим каталогом — см. /models).',
        code: 'curl https://api.brikko.ru/v1/chat/completions \\\n  -H "Authorization: Bearer sk-brk-..." \\\n  -H "Content-Type: application/json" \\\n  -d \'{\n    "model": "gpt-5-mini",\n    "messages": [{"role":"user","content":"ping"}]\n  }\'',
        language: 'bash',
      },
    ],
    common_issues: [
      {
        q: 'Cursor возвращает 401 Unauthorized',
        a: 'Проверьте префикс ключа — должен начинаться с sk-brk-. Если копировали из дашборда — удалите пробел в конце. Ключи без префикса sk- Cursor отвергает на уровне валидации.',
      },
      {
        q: 'Tab-completion (Cmd+K) не работает, чат работает',
        a: 'Cursor для inline-completion использует свою отдельную модель cursor-small, которая не настраивается через Override. Это ограничение Cursor, не Brikko. Через наш ключ работают: Composer (Cmd+L), Apply, Edit, Agent. Для inline — Cursor Pro по их подписке.',
      },
      {
        q: 'Модель Claude Opus 4.7 не появляется в списке',
        a: 'В Cursor → Models → нажмите "+ Add Model" → введите claude-opus-4-7 и сохраните. Cursor хранит whitelist моделей вручную для override-ключей.',
      },
      {
        q: 'Запрос идёт долго, иногда таймаут',
        a: 'Включите smart routing в дашборде Brikko (Settings → Routing). При 429 от провайдера запрос автоматически уйдёт в резервную модель того же класса — у Cursor таймаут 60 сек, наш failover укладывается за 2-3 сек.',
      },
    ],
    related: ['claude-code', 'cline'],
    seo_title: 'Cursor IDE с Brikko API · 5 минут на настройку, оплата в рублях',
    seo_description:
      'Подключите Cursor IDE к OpenAI и Anthropic через Brikko: смена baseURL и API-ключа, оплата ЮKassa, чек НПД, PII-маскинг по 152-ФЗ.',
  },

  'claude-code': {
    slug: 'claude-code',
    name: 'Claude Code CLI',
    icon: 'claude-code',
    tagline: 'CLI-агент Anthropic в терминале — с рублёвой оплатой через Brikko.',
    description:
      'Claude Code — официальный CLI-агент Anthropic для разработки в терминале. Читает файлы, запускает команды, правит код, открывает PR. По умолчанию ходит на api.anthropic.com — для российской компании это означает оплату с зарубежной карты и прокидывание исходников в США без ДГПД. Brikko поддерживает нативный Anthropic Messages API на endpoint /v1/messages — Claude Code не понимает, что говорит не с Anthropic, а вы получаете рублёвую оплату, чек НПД после каждого пополнения и журнал запросов в дашборде. PII-маскинг включается флагом и фильтрует ФИО/телефоны/паспорта в промптах перед отправкой в Anthropic.',
    why_brikko: [
      'Нативный Anthropic /v1/messages — Claude Code работает без модификаций, переменные окружения те же что в их доках.',
      'Прозрачные траты: видите каждый запрос Claude Code в /usage, можете лимитировать ключ суточным бюджетом.',
      'Failover OpenAI ↔ Anthropic: если Anthropic временно недоступен, агент переключится на gpt-5 без потери контекста разговора.',
    ],
    steps: [
      {
        title: 'Шаг 1. Установить Claude Code и получить ключ Brikko',
        body: 'Установите CLI по официальной инструкции Anthropic (npm install -g @anthropic-ai/claude-code). В дашборде brikko.ru создайте API-ключ. Для Claude Code рекомендуем выпустить отдельный ключ с budget 5000 ₽/мес — агент в режиме автономии может потратить много за час.',
        code: 'npm install -g @anthropic-ai/claude-code\nclaude --version',
        language: 'bash',
      },
      {
        title: 'Шаг 2. Настроить переменные окружения',
        body: 'Claude Code читает ANTHROPIC_BASE_URL и ANTHROPIC_AUTH_TOKEN. Пропишите в ~/.zshrc или ~/.bashrc, либо в .env проекта. Внимание: переменная называется ANTHROPIC_AUTH_TOKEN, а не ANTHROPIC_API_KEY — это особенность Claude Code.',
        code: 'export ANTHROPIC_BASE_URL=https://api.brikko.ru\nexport ANTHROPIC_AUTH_TOKEN=sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nexport ANTHROPIC_MODEL=claude-opus-4-7\nexport ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5',
        language: 'bash',
      },
      {
        title: 'Шаг 3. Запустить Claude Code в проекте',
        body: 'Перейдите в папку проекта, выполните claude. Если CLI стартует без ошибок и пишет "Connected to Anthropic" — всё подключено. Дайте простую задачу для проверки: "прочти README.md и расскажи о проекте". Если ответ пришёл — гайд завершён.',
        code: 'cd /path/to/your/project\nclaude\n\n# В чате:\n> прочти README.md и кратко расскажи о проекте',
        language: 'bash',
      },
      {
        title: 'Шаг 4. Проверить расход через дашборд',
        body: 'Откройте brikko.ru/app/usage — там увидите запросы в реальном времени с моделью, токенами и ценой в рублях. Отдельный фильтр по ключу — удобно отделять траты Claude Code от других интеграций.',
        code: null,
      },
    ],
    common_issues: [
      {
        q: 'Claude Code пишет "Invalid API key"',
        a: 'Проверьте имя переменной — должна быть ANTHROPIC_AUTH_TOKEN (не ANTHROPIC_API_KEY). Также убедитесь что в ANTHROPIC_BASE_URL нет /v1 на конце — Claude Code добавляет /v1/messages сам.',
      },
      {
        q: 'Агент работает, но иногда "model overloaded"',
        a: 'Это 429 от Anthropic — у них есть rate limits на минуту. Включите routing-preset auto:smart-fallback в Brikko — на 429 запрос пойдёт через резервный канал Anthropic, а если и он занят — gpt-5 эквивалентного класса.',
      },
      {
        q: 'Хочу использовать Claude Sonnet, а не Opus, для экономии',
        a: 'Передайте модель явно: ANTHROPIC_MODEL=claude-sonnet-4-6. Sonnet 4.6 в нашем каталоге примерно в 5 раз дешевле Opus 4.7 при сравнимом качестве для рутинных задач.',
      },
      {
        q: 'Можно ли ограничить расход одного агента?',
        a: 'Да. В дашборде создайте отдельный API-ключ для Claude Code с daily budget (например, 500 ₽/день). Агент получит 402 Payment Required при превышении — не сожжёт месячный бюджет за ночь.',
      },
    ],
    related: ['cursor', 'cline'],
    seo_title: 'Claude Code в России · подключение через Brikko с рублёвой оплатой',
    seo_description:
      'Claude Code CLI с оплатой в рублях через Brikko: ANTHROPIC_BASE_URL, нативный /v1/messages, чек НПД, лимиты бюджета.',
  },

  cline: {
    slug: 'cline',
    name: 'Cline (VS Code)',
    icon: 'cline',
    tagline: 'Open-source AI-агент для VS Code — без vendor lock-in Cursor.',
    description:
      'Cline — расширение VS Code с агент-режимом: читает файлы, выполняет команды, просит подтверждение перед записью. Альтернатива Cursor, если не хотите менять редактор или нужен open-source. Cline поддерживает любые OpenAI-совместимые провайдеры — Brikko подключается через preset "OpenAI Compatible". Можно выбрать GPT-5, Claude Opus 4.7 или DeepSeek в зависимости от задачи: для дешёвой массовой генерации — DeepSeek (в 4 раза дешевле GPT-5), для сложных рефакторингов — Claude Opus.',
    why_brikko: [
      'Cline через нас умеет переключать модели на лету — GPT-5 для архитектуры, DeepSeek V3.2 для рутины. Один ключ, разные модели.',
      'Ключ можно ограничить бюджетом (например, 1000 ₽/мес) — агент не сожжёт месячные деньги за час.',
      'Если выпадет один провайдер — failover отрабатывает прозрачно, агент не теряет контекст разговора.',
    ],
    steps: [
      {
        title: 'Шаг 1. Установить Cline в VS Code',
        body: 'Откройте VS Code → Extensions → найдите "Cline" (автор: saoudrizwan). Установите. После установки слева появится иконка Cline — нажмите её.',
        code: null,
      },
      {
        title: 'Шаг 2. Выбрать API-провайдер',
        body: 'В настройках Cline: API Provider → "OpenAI Compatible". Это режим для любого OpenAI-совместимого endpoint — наш как раз такой.',
        code: null,
      },
      {
        title: 'Шаг 3. Заполнить поля',
        body: 'Все четыре поля обязательны. Model ID — точное имя модели из нашего каталога /models. Если опечатка — получите 404.',
        code: 'Base URL:    https://api.brikko.ru/v1\nAPI Key:     sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nModel ID:    claude-sonnet-4-6\nModel Info:  Context 200000, Max Output 8192, Supports Images: yes',
        language: 'env',
      },
      {
        title: 'Шаг 4. Проверить',
        body: 'В чате Cline напишите простую задачу: "посмотри файл package.json и скажи какая версия Node нужна". Cline должен прочитать файл, дать ответ, спросить подтверждение если хочет что-то изменить. Если запрос отвалился — проверьте Output → Cline для логов.',
        code: null,
      },
    ],
    common_issues: [
      {
        q: 'Cline пишет "model does not support tool use"',
        a: 'Cline требует function calling. Все модели OpenAI gpt-5*, Claude 4.6/4.7, Gemini 3.x — поддерживают. DeepSeek V3.2 Chat — да. YandexGPT и GigaChat пока без tool calling — для Cline их не выбирайте.',
      },
      {
        q: 'Хочу использовать Claude через нативный /v1/messages, а не /v1/chat/completions',
        a: 'Cline 3.x умеет нативный Anthropic. Выберите API Provider "Anthropic", укажите Base URL https://api.brikko.ru (без /v1), API Key — наш sk-brk-. Cline пошлёт на /v1/messages напрямую, без OpenAI-обёртки.',
      },
      {
        q: 'Запросы дорогие, как сократить расход',
        a: 'Cline шлёт весь контекст разговора в каждом запросе. Включите prompt caching в дашборде Brikko — повторные части системного промпта и истории кэшируются, экономия до 90% на input-токенах. Работает для Claude и GPT-5 семейства.',
      },
      {
        q: 'Как переключиться между моделями быстро',
        a: 'В Cline: Settings → Model ID меняется в любой момент. Удобный паттерн — два профиля: "cheap" с deepseek-v3.2 и "smart" с claude-opus-4-7. Cline сохраняет контекст при переключении.',
      },
    ],
    related: ['cursor', 'claude-code'],
    seo_title: 'Cline для VS Code с Brikko · open-source альтернатива Cursor',
    seo_description:
      'Подключите Cline к Brikko через OpenAI Compatible: GPT-5, Claude, DeepSeek в одном ключе. Оплата в рублях, лимиты бюджета.',
  },

  langchain: {
    slug: 'langchain',
    name: 'LangChain',
    icon: 'langchain',
    tagline: 'LangChain Python и JS — переключение на Brikko в одну строку.',
    description:
      'LangChain — фреймворк для построения LLM-приложений: цепочки, агенты, RAG, память. Python и TypeScript SDK. Если ваше приложение уже работает с ChatOpenAI или ChatAnthropic — переход на Brikko стоит одного аргумента: base_url. Весь остальной код LangChain (PromptTemplate, OutputParser, AgentExecutor, retrievers) остаётся как был. Это значит, что миграция с прямого OpenAI на легальный российский шлюз для LangChain-приложения занимает 5 минут и не требует переписывать prompt engineering.',
    why_brikko: [
      'Совместимость с langchain_openai.ChatOpenAI и langchain_anthropic.ChatAnthropic — меняется только base_url и api_key.',
      'Streaming, function calling, structured outputs (with_structured_output, JSON Schema strict) — поддерживаются как в оригинальных SDK.',
      'Один LangChain-проект может смешивать GPT-5, Claude и DeepSeek через Brikko с одним ключом — для разных шагов цепочки разные модели.',
    ],
    steps: [
      {
        title: 'Шаг 1. Установить LangChain',
        body: 'Если ещё не установлен — поставьте langchain-openai (для GPT) или langchain-anthropic (для Claude). Brikko совместим с обоими SDK.',
        code: 'pip install langchain langchain-openai langchain-anthropic\n# или TypeScript\nnpm install langchain @langchain/openai @langchain/anthropic',
        language: 'bash',
      },
      {
        title: 'Шаг 2. Настроить ChatOpenAI (для GPT-5, DeepSeek, Yandex, GigaChat)',
        body: 'Передайте base_url и api_key в конструктор. Все модели нашего каталога с openai-совместимым форматом доступны через ChatOpenAI — выбираете моделью в параметре model.',
        code: 'from langchain_openai import ChatOpenAI\n\nllm = ChatOpenAI(\n    model="gpt-5-mini",\n    api_key="sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",\n    base_url="https://api.brikko.ru/v1",\n    temperature=0.7,\n)\n\nresponse = llm.invoke("Привет, как дела?")\nprint(response.content)',
        language: 'python',
      },
      {
        title: 'Шаг 3. Настроить ChatAnthropic (для Claude через нативный /v1/messages)',
        body: 'Если вы используете Claude и хотите нативный Anthropic API (а не OpenAI-обёртку) — Brikko его поддерживает. Передаётся base_url без /v1 на конце — SDK сам добавит /v1/messages.',
        code: 'from langchain_anthropic import ChatAnthropic\n\nllm = ChatAnthropic(\n    model="claude-opus-4-7",\n    api_key="sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",\n    base_url="https://api.brikko.ru",\n    max_tokens=4096,\n)\n\nresponse = llm.invoke("Объясни в 3 предложениях, что такое RAG")\nprint(response.content)',
        language: 'python',
      },
      {
        title: 'Шаг 4. Использовать в агентах и цепочках без изменений',
        body: 'После того как llm создан — все стандартные паттерны LangChain работают: AgentExecutor, RunnableSequence, with_structured_output, tools. Brikko прозрачен для верхнего уровня кода.',
        code: 'from langchain.agents import AgentExecutor, create_tool_calling_agent\nfrom langchain_core.prompts import ChatPromptTemplate\n\nprompt = ChatPromptTemplate.from_messages([\n    ("system", "Ты ассистент. Используй инструменты."),\n    ("user", "{input}"),\n    ("placeholder", "{agent_scratchpad}"),\n])\n\nagent = create_tool_calling_agent(llm, tools=[...], prompt=prompt)\nexecutor = AgentExecutor(agent=agent, tools=[...])\nexecutor.invoke({"input": "запрос пользователя"})',
        language: 'python',
      },
    ],
    common_issues: [
      {
        q: 'with_structured_output() возвращает невалидный JSON для DeepSeek/Yandex',
        a: 'Не все модели поддерживают strict JSON Schema. Для гарантии — используйте gpt-5/gpt-5-mini или claude-opus-4-7/claude-sonnet-4-6. На странице /models у каждой модели есть тег "Strict JSON" — фильтруйте по нему.',
      },
      {
        q: 'streaming работает в langchain_openai, не работает в langchain_anthropic',
        a: 'Это известный баг старых версий langchain-anthropic (<0.3). Обновите: pip install -U langchain-anthropic. Streaming через нашу прокси работает идентично прямому Anthropic SDK — мы не модифицируем SSE-поток.',
      },
      {
        q: 'Хочу логировать каждый вызов LLM в один файл',
        a: 'Используйте LangSmith или собственный callback-handler — Brikko не накладывает ограничений на коллбэки. Также в дашборде brikko.ru/usage есть фильтр по api_key — выпустите отдельный ключ для LangChain-приложения и видите все его запросы.',
      },
      {
        q: 'Как использовать prompt caching из LangChain',
        a: 'Для Claude через ChatAnthropic — добавьте cache_control в system message: SystemMessage(content=[{"type":"text","text":"...","cache_control":{"type":"ephemeral"}}]). Brikko прокидывает кэш-флаги в Anthropic 1:1, экономия до 90% на input.',
      },
    ],
    related: ['cursor', 'claude-code'],
    seo_title: 'LangChain в России · ChatOpenAI и ChatAnthropic через Brikko',
    seo_description:
      'Подключите LangChain Python/JS к Brikko: base_url + api_key, совместимо с langchain_openai и langchain_anthropic, чек НПД, рублёвая касса.',
  },

  n8n: {
    slug: 'n8n',
    name: 'n8n',
    icon: 'n8n',
    tagline: 'AI-узлы n8n с GPT-5 и Claude — без VPN и зарубежных карт.',
    description:
      'n8n — open-source платформа для автоматизации (альтернатива Zapier, self-hosted). В n8n есть нативные AI-узлы: OpenAI Chat Model, Anthropic Chat Model, AI Agent. По умолчанию они требуют ключи прямых провайдеров — для российской компании это путь через VPN и зарубежную карту, без чеков. Brikko подменяет endpoint в credential одним полем — все узлы AI Agent / Chat работают, как и раньше, но платят рублями и пишут логи в /usage. Подходит операционным командам, которые автоматизируют без программистов: HR-обработка резюме, первичная квалификация лидов, авто-теги в support-тикетах.',
    why_brikko: [
      'OpenAI Chat и Anthropic Chat узлы поддерживают custom Base URL — переключение в credentials, без правки workflows.',
      'Лимиты по ключу: дайте n8n-инстансу ключ с budget 5000 ₽/мес — не сожжёт бюджет ночью если кто-то зациклит workflow.',
      'AI Agent с Tools (function calling) — поддерживается на всех наших OpenAI и Claude моделях, JSON Schema strict.',
    ],
    steps: [
      {
        title: 'Шаг 1. Создать ключ Brikko для n8n',
        body: 'В дашборде Brikko создайте отдельный API-ключ с пометкой "n8n production" и daily budget 500 ₽. Это страховка: если workflow зациклится в 3 ночи, утром увидите 402 Payment Required, а не пустой счёт.',
        code: null,
      },
      {
        title: 'Шаг 2. Создать credential в n8n: OpenAI',
        body: 'В n8n: Credentials → Create New → "OpenAI". В настройках credential найдите "Base URL" (в свежих версиях n8n это под Advanced). Замените стандартный на наш.',
        code: 'API Key:   sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nBase URL:  https://api.brikko.ru/v1\nOrganization ID:  (оставить пустым)',
        language: 'env',
      },
      {
        title: 'Шаг 3. Использовать в OpenAI Chat Model узле',
        body: 'В workflow добавьте узел "AI Agent" → выберите Chat Model: OpenAI. В credential выберите свежесозданный Brikko credential. В поле Model — модель из нашего каталога. Для не-разработчиков рекомендуем gpt-5-mini для рутины и claude-sonnet-4-6 если важно качество ответа.',
        code: null,
      },
      {
        title: 'Шаг 4. Тестовый прогон',
        body: 'Запустите workflow на одном тестовом входе. В Execution Log смотрите узел AI: response, tokens, latency. Если всё ОК — копируйте credential на production-инстанс. В дашборде Brikko этот же запрос видно через 1-2 секунды в /usage.',
        code: null,
      },
    ],
    common_issues: [
      {
        q: 'В credential нет поля Base URL',
        a: 'Поле появилось в n8n начиная с v1.18. На старых версиях — обновите n8n или используйте узел "HTTP Request" вместо OpenAI Chat (мы дадим curl-эквивалент в /docs).',
      },
      {
        q: 'AI Agent с Tools падает на 400 Bad Request',
        a: 'Проверьте что выбранная модель поддерживает function calling. В нашем каталоге это все gpt-5*, o3, o4-mini, claude-4.5/4.6/4.7, gemini 3.x. YandexGPT и GigaChat пока без tools — для Agent-узлов их не используйте.',
      },
      {
        q: 'Запросы дорогие, в месяц улетает много',
        a: 'Включите prompt caching на тариф (Pro Features и выше) — для повторяющихся system prompts экономия до 90%. Также: для простых задач (классификация, теги) хватает gpt-5-mini, не используйте gpt-5 везде по умолчанию.',
      },
      {
        q: 'Хочу embeddings для RAG в n8n',
        a: 'Узел "Embeddings OpenAI" → тот же credential. Модели: text-embedding-3-small (дёшево, для большинства RAG) или text-embedding-3-large (точнее). В V2 добавим embedding-модели Yandex.',
      },
    ],
    related: ['langchain', 'cursor'],
    seo_title: 'n8n + AI в России · OpenAI и Claude через Brikko, оплата ЮKassa',
    seo_description:
      'Подключите AI-узлы n8n (OpenAI Chat, Anthropic Chat, AI Agent) к Brikko: смена Base URL в credentials, чек НПД, лимиты бюджета.',
  },

  openclaw: {
    slug: 'openclaw',
    name: 'OpenClaw',
    icon: 'openclaw',
    tagline: 'Telegram-агент OpenClaw — Claude и GPT в чате через Brikko.',
    description:
      'OpenClaw — open-source Telegram-бот, превращающий чат в AI-агента: пишете в Telegram, OpenClaw держит контекст, ходит за вас в OpenAI/Anthropic, отдаёт результат. Удобно для команд, которые хотят AI без отдельного интерфейса — всё в Telegram. По умолчанию OpenClaw настроен на api.openai.com и api.anthropic.com — Brikko подменяется через переменные окружения в .env. Особенно удобно для агентств: один бот в TG-канале, общий ключ Brikko с месячным бюджетом, видимость в /usage кто из команды сколько потратил по своим API-ключам OpenClaw.',
    why_brikko: [
      'OpenClaw поддерживает custom OpenAI и Anthropic endpoints — оба совместимы с Brikko из коробки.',
      'Отдельный API-ключ Brikko на инстанс OpenClaw — лимит трат, история в /usage, не пересекается с другими интеграциями.',
      'Failover между OpenAI и Anthropic — если один провайдер недоступен, разговор продолжается на резервной модели прозрачно.',
    ],
    steps: [
      {
        title: 'Шаг 1. Развернуть OpenClaw',
        body: 'Клонируйте репозиторий OpenClaw, установите зависимости. Документацию по созданию Telegram-бота через @BotFather пропускаем — она есть в README OpenClaw.',
        code: 'git clone https://github.com/openclaw/openclaw.git\ncd openclaw\ncp .env.example .env\nnpm install',
        language: 'bash',
      },
      {
        title: 'Шаг 2. Прописать Brikko в .env',
        body: 'OpenClaw читает OPENAI_BASE_URL и ANTHROPIC_BASE_URL из окружения. Можно подключить оба сразу — OpenClaw сам выберет по выбранной модели.',
        code: 'TELEGRAM_BOT_TOKEN=12345:abc...\n\n# Brikko как OpenAI-провайдер\nOPENAI_API_KEY=sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nOPENAI_BASE_URL=https://api.brikko.ru/v1\n\n# Brikko как Anthropic-провайдер (тот же ключ работает)\nANTHROPIC_API_KEY=sk-brk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nANTHROPIC_BASE_URL=https://api.brikko.ru\n\nDEFAULT_MODEL=claude-sonnet-4-6',
        language: 'env',
      },
      {
        title: 'Шаг 3. Запустить и пригласить в TG-чат',
        body: 'npm start. Откройте бота в Telegram (имя из BotFather), напишите /start. Если бот ответил — соединение с Brikko работает. Команда /model claude-opus-4-7 переключает модель в чате.',
        code: 'npm start\n\n# В Telegram:\n/start\n/model claude-opus-4-7\nПривет! Объясни как работает retrieval-augmented generation.',
        language: 'bash',
      },
      {
        title: 'Шаг 4. Контролировать расход',
        body: 'В дашборде Brikko по этому ключу видна каждая отправка из бота. Если бот в командном чате — поставьте daily budget 500 ₽ или больше — это страховка от случайных длинных диалогов с Claude Opus.',
        code: null,
      },
    ],
    common_issues: [
      {
        q: 'OpenClaw возвращает "model not found" для claude-*',
        a: 'OpenClaw по умолчанию шлёт Anthropic-модели на /v1/chat/completions (OpenAI-формат). Включите в .env: ANTHROPIC_NATIVE=true — OpenClaw начнёт ходить на /v1/messages нативно. Brikko это поддерживает.',
      },
      {
        q: 'Команда /image не работает',
        a: 'Image generation в Brikko на момент написания — в V2 (Q3 2026). Для голосовой расшифровки и текста OpenClaw работает прямо сейчас. Список фич Brikko по релизам — на /roadmap.',
      },
      {
        q: 'Как сделать так, чтобы у каждого пользователя бота был свой бюджет',
        a: 'OpenClaw поддерживает per-user API keys. На тарифе Team в Brikko можно выпустить до 50 ключей — раздайте каждому участнику чата свой, тогда расход будет виден в /usage по каждому. На Pro тарифе — общий ключ с budget на бот в целом.',
      },
      {
        q: 'Бот тормозит на длинных разговорах',
        a: 'OpenClaw шлёт всю историю в каждый запрос. Включите prompt caching в дашборде Brikko (Pro Features+) — повторяющиеся первые N токенов истории кэшируются на стороне провайдера, экономия до 90% и латентность ниже на 30-50%.',
      },
    ],
    related: ['claude-code', 'cline'],
    seo_title: 'OpenClaw в Telegram · Claude и GPT через Brikko, рублёвая оплата',
    seo_description:
      'Подключите OpenClaw Telegram-агент к Brikko: OPENAI_BASE_URL и ANTHROPIC_BASE_URL в .env, лимиты бюджета, /usage по каждому пользователю.',
  },
} satisfies Record<string, ToolGuide>;

// Порядок отображения в /tools (по приоритету для нашего ICP)
export const TOOL_GUIDES_LIST: ToolGuide[] = [
  TOOL_GUIDES.cursor,
  TOOL_GUIDES['claude-code'],
  TOOL_GUIDES.cline,
  TOOL_GUIDES.langchain,
  TOOL_GUIDES.n8n,
  TOOL_GUIDES.openclaw,
];
