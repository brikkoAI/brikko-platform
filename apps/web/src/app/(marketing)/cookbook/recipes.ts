/**
 * Cookbook recipes — 5 готовых JSON Schema use-cases для разработчиков.
 *
 * Цели:
 *   - SEO: 5 длинных страниц с techical-контентом и H1 под ключевые слова РФ-сегмента.
 *   - Полезный контент: разработчик копирует curl + system prompt + schema → запускает
 *     у себя за 2 минуты.
 *   - Подвести к /signup: «Попробовать в кабинете».
 *
 * Стилистика:
 *   - Без имён конкурентов поимённо (legal: ст. 14.1 135-ФЗ + 152 ГК).
 *   - Без слов «единственный», «революционный».
 *   - Curl-сниппеты — на api.brikko.ru/v1/chat/completions, OpenAI-compat.
 */

export interface Recipe {
  slug: string;
  title: string;
  shortTitle: string;
  metaDescription: string;
  intro: string; // 1-2 предложения «зачем это»
  description: string; // 1 параграф «что под капотом»
  schemaName: string;
  jsonSchema: string; // pretty-printed JSON
  systemPrompt: string;
  exampleInput: string;
  exampleOutput: string; // pretty-printed JSON
  curlSnippet: string;
  notes?: string; // опционально: важные оговорки
}

const COMMON_CURL_HEADERS = `  -H "Authorization: Bearer $BRIKKO_API_KEY" \\
  -H "Content-Type: application/json" \\`;

export const RECIPES: Recipe[] = [
  // 1
  {
    slug: 'lead-qualification',
    shortTitle: 'Квалификация B2B-лида',
    title: 'Квалификация лида в B2B SaaS',
    metaDescription:
      'Готовый JSON Schema рецепт квалификации B2B-лида: company_size, budget, timeline, fit_score. Промпт + curl-пример для GPT/Claude/Gemini через api.brikko.ru.',
    intro:
      'Превратить свободный ответ из формы «Расскажите о компании» в структурированный объект, который можно сразу класть в CRM и сортировать по fit_score.',
    description:
      'Используем strict JSON Schema, чтобы модель не возвращала комментарии или null в неожиданных полях. fit_score (0-10) — взвешенная оценка по company_size, budget_range и decision_timeline; используем поле для маршрутизации лида в SDR / AE / nurture.',
    schemaName: 'lead_qualification',
    jsonSchema: `{
  "name": "lead_qualification",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "company_size",
      "budget_range",
      "decision_timeline",
      "pain_points",
      "fit_score"
    ],
    "properties": {
      "company_size": {
        "type": "string",
        "enum": ["1-10", "11-50", "51-200", "201-1000", "1000+"]
      },
      "budget_range": {
        "type": "string",
        "enum": ["<100k", "100k-500k", "500k-2M", "2M-10M", "10M+", "unknown"]
      },
      "decision_timeline": {
        "type": "string",
        "enum": ["this_month", "1-3_months", "3-6_months", "6+_months", "unknown"]
      },
      "pain_points": {
        "type": "array",
        "items": { "type": "string" },
        "maxItems": 5
      },
      "fit_score": {
        "type": "integer",
        "minimum": 0,
        "maximum": 10
      }
    }
  }
}`,
    systemPrompt: `Ты — sales-аналитик B2B SaaS. На вход — свободный текст от потенциального клиента. На выход — строго валидный JSON по схеме lead_qualification.

Правила:
- pain_points — короткие фразы 2-5 слов на русском.
- budget_range — в рублях / год (если упомянут $-бюджет, конвертируй по курсу 80 ₽).
- fit_score — 8-10 если company_size ≥ 51 И budget ≥ 500k И timeline ≤ 3_months; 5-7 если 2 из 3; иначе 0-4.
- Если поле не упомянуто — ставь "unknown" (для enum) или пустой массив (для arrays). Никогда не выдумывай.`,
    exampleInput:
      '«Привет, мы интернет-магазин одежды, 80 человек в команде. Хотим автоматизировать поддержку клиентов через AI — устали ручками отвечать на одни и те же вопросы. Бюджет ориентировочно 1.5 млн в год, хотим запустить пилот в течение пары месяцев.»',
    exampleOutput: `{
  "company_size": "51-200",
  "budget_range": "500k-2M",
  "decision_timeline": "1-3_months",
  "pain_points": [
    "ручная поддержка клиентов",
    "повторяющиеся вопросы",
    "масштабирование саппорта"
  ],
  "fit_score": 9
}`,
    curlSnippet: `curl https://api.brikko.ru/v1/chat/completions \\
${COMMON_CURL_HEADERS}
  -d '{
    "model": "auto:smart",
    "messages": [
      { "role": "system", "content": "<system prompt из этой страницы>" },
      { "role": "user", "content": "<свободный ответ лида>" }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": <schema из этой страницы>
    }
  }'`,
  },

  // 2
  {
    slug: 'crm-enrichment',
    shortTitle: 'Обогащение CRM-карточки',
    title: 'Обогащение CRM-карточки из свободного текста',
    metaDescription:
      'Парсинг подписи из e-mail в готовую CRM-запись: ИНН, КПП, ОГРН, ФИО, отрасль. Готовый JSON Schema рецепт для api.brikko.ru.',
    intro:
      'Подпись из входящего e-mail или ответа на форму превращается в готовый объект для CRM — без ручного копирования полей и без regex по 50 паттернам.',
    description:
      'Strict JSON Schema гарантирует корректные форматы российских реквизитов: ИНН (10 или 12 цифр), КПП (9 цифр), ОГРН (13 или 15 цифр). Поле industry — enum под МКБ-2 / ОКВЭД-агрегаты, поле employee_count_range — закрытый список диапазонов.',
    schemaName: 'crm_contact_enrichment',
    jsonSchema: `{
  "name": "crm_contact_enrichment",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["full_name", "industry", "employee_count_range"],
    "properties": {
      "inn": {
        "type": "string",
        "pattern": "^[0-9]{10}$|^[0-9]{12}$"
      },
      "kpp": {
        "type": "string",
        "pattern": "^[0-9]{9}$"
      },
      "ogrn": {
        "type": "string",
        "pattern": "^[0-9]{13}$|^[0-9]{15}$"
      },
      "full_name": { "type": "string" },
      "industry": {
        "type": "string",
        "enum": [
          "retail",
          "manufacturing",
          "fintech",
          "logistics",
          "healthcare",
          "education",
          "saas",
          "media",
          "construction",
          "agriculture",
          "other"
        ]
      },
      "employee_count_range": {
        "type": "string",
        "enum": ["1-10", "11-50", "51-200", "201-1000", "1000+", "unknown"]
      }
    }
  }
}`,
    systemPrompt: `Ты — парсер CRM-данных. На вход — свободный текст (подпись из e-mail, шапка договора, или текст с сайта). На выход — строго валидный JSON по схеме crm_contact_enrichment.

Правила:
- ИНН/КПП/ОГРН извлекай только если они явно указаны и проходят формат-проверку. Не выдумывай.
- full_name — предпочитай ФИО физлица (если есть подпись), иначе — название компании.
- industry — выбирай ближайшее значение из enum по описанию деятельности; если непонятно — "other".
- employee_count_range — "unknown", если не упомянуто прямо или косвенно.`,
    exampleInput: `«С уважением,
Анна Петрова, директор по маркетингу
ООО «Северный Ритейл» (ИНН 7727123456, КПП 772701001)
Сеть продуктовых магазинов, 120 сотрудников
www.severny-retail.ru»`,
    exampleOutput: `{
  "inn": "7727123456",
  "kpp": "772701001",
  "full_name": "Анна Петрова",
  "industry": "retail",
  "employee_count_range": "51-200"
}`,
    curlSnippet: `curl https://api.brikko.ru/v1/chat/completions \\
${COMMON_CURL_HEADERS}
  -d '{
    "model": "auto:cheap",
    "messages": [
      { "role": "system", "content": "<system prompt из этой страницы>" },
      { "role": "user", "content": "<подпись или фрагмент e-mail>" }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": <schema из этой страницы>
    }
  }'`,
  },

  // 3
  {
    slug: 'contract-parsing',
    shortTitle: 'Парсинг условий договора',
    title: 'Парсинг ключевых условий договора',
    metaDescription:
      'PDF-договор → структурированные поля: стороны, сумма, валюта, срок, юрисдикция. JSON Schema рецепт для GPT-5.5 / Claude / Gemini через api.brikko.ru.',
    intro:
      'Загружаешь текст договора (PDF → text), получаешь готовые поля для CRM и финансовой системы — без ручного выписывания сумм и сроков.',
    description:
      'Strict JSON Schema с типами number / date / boolean — модель не может вернуть «1 500 000 рублей» как строку. parties — массив юр.лиц / физ.лиц с ролью. Если поле не определено в тексте — модель явно это сообщает (см. system prompt).',
    schemaName: 'contract_parsing',
    jsonSchema: `{
  "name": "contract_parsing",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "parties",
      "contract_value",
      "currency",
      "signing_date",
      "payment_terms",
      "jurisdiction",
      "termination_clause"
    ],
    "properties": {
      "parties": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["name", "role"],
          "properties": {
            "name": { "type": "string" },
            "role": {
              "type": "string",
              "enum": ["customer", "supplier", "guarantor", "other"]
            }
          }
        },
        "minItems": 2
      },
      "contract_value": { "type": "number" },
      "currency": {
        "type": "string",
        "enum": ["RUB", "USD", "EUR", "CNY", "other"]
      },
      "signing_date": {
        "type": "string",
        "format": "date"
      },
      "payment_terms": { "type": "string" },
      "jurisdiction": { "type": "string" },
      "termination_clause": { "type": "boolean" }
    }
  }
}`,
    systemPrompt: `Ты — юридический ассистент. На вход — текст договора. Извлекай ключевые поля строго по схеме contract_parsing.

Правила:
- contract_value — общая сумма договора в numerical. Если только месячная — оцени годовую и пометь это в payment_terms.
- signing_date — формат YYYY-MM-DD. Если в договоре дата прописью — конвертируй.
- jurisdiction — место разрешения споров (например, «Арбитражный суд г. Москвы»).
- termination_clause — true, если есть пункт о досрочном расторжении одной из сторон.
- Если поле невозможно извлечь однозначно — поставь contract_value: 0, payment_terms: "не указано в тексте", и т.п. Не галлюцинируй.`,
    exampleInput:
      '«Договор оказания услуг №42 от 15 марта 2026 г. ООО "Альфа Технологии" (Заказчик) и ИП Иванов И.И. (Исполнитель) заключили настоящий договор на сумму 2 400 000 (Два миллиона четыреста тысяч) рублей. Оплата ежемесячно по 200 000 рублей. Юрисдикция — Арбитражный суд г. Москвы. Любая из сторон может расторгнуть договор с уведомлением за 30 дней.»',
    exampleOutput: `{
  "parties": [
    { "name": "ООО \\"Альфа Технологии\\"", "role": "customer" },
    { "name": "ИП Иванов И.И.", "role": "supplier" }
  ],
  "contract_value": 2400000,
  "currency": "RUB",
  "signing_date": "2026-03-15",
  "payment_terms": "Ежемесячно 200 000 ₽ в течение 12 месяцев",
  "jurisdiction": "Арбитражный суд г. Москвы",
  "termination_clause": true
}`,
    curlSnippet: `curl https://api.brikko.ru/v1/chat/completions \\
${COMMON_CURL_HEADERS}
  -d '{
    "model": "auto:smart",
    "messages": [
      { "role": "system", "content": "<system prompt из этой страницы>" },
      { "role": "user", "content": "<полный текст договора>" }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": <schema из этой страницы>
    }
  }'`,
    notes:
      'Для длинных договоров (>30 страниц) рекомендуем модели с большим context window: GPT-5.5, Claude Opus 4.7 или Gemini 3.1 Pro. model: "auto:smart" сам выберет подходящую.',
  },

  // 4
  {
    slug: 'support-ticket-classifier',
    shortTitle: 'Классификация support-тикетов',
    title: 'Классификация support-тикетов',
    metaDescription:
      'Входящий e-mail → routing в нужный отдел: категория, severity, sentiment, summary. Готовый JSON Schema рецепт для api.brikko.ru.',
    intro:
      'Входящие тикеты автоматически попадают в нужный отдел: billing, tech, account, sales. Severity определяется по тексту, не по самооценке клиента.',
    description:
      'Strict JSON Schema + summary_50chars — модель обязана уложиться в краткое описание для UI-списка тикетов. requires_human — флаг для тикетов, где AI-ответ нежелателен (юридические претензии, угрозы оттока).',
    schemaName: 'support_ticket_classifier',
    jsonSchema: `{
  "name": "support_ticket_classifier",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "category",
      "severity",
      "sentiment",
      "requires_human",
      "suggested_assignee_role",
      "summary_50chars"
    ],
    "properties": {
      "category": {
        "type": "string",
        "enum": ["billing", "technical", "account", "feature_request", "sales", "other"]
      },
      "severity": {
        "type": "string",
        "enum": ["low", "medium", "high", "critical"]
      },
      "sentiment": {
        "type": "string",
        "enum": ["positive", "neutral", "frustrated", "angry"]
      },
      "requires_human": { "type": "boolean" },
      "suggested_assignee_role": {
        "type": "string",
        "enum": ["billing_specialist", "tech_support_l1", "tech_support_l2", "account_manager", "sales", "legal"]
      },
      "summary_50chars": {
        "type": "string",
        "maxLength": 50
      }
    }
  }
}`,
    systemPrompt: `Ты — диспетчер support. Классифицируй входящий тикет по схеме support_ticket_classifier.

Правила:
- severity:critical — если упомянут полный отказ системы / потеря денег / угроза contract churn.
- requires_human:true — если есть юридические претензии, угрозы суда, упоминание ФАС/Роспотребнадзора, или текст явно эмоциональный.
- summary_50chars — суть проблемы, до 50 символов с пробелами, без эмодзи.
- suggested_assignee_role — самый подходящий из enum под содержание тикета.`,
    exampleInput:
      '«Здравствуйте! Третий день не приходит счёт за апрель. Бухгалтерия ругается, а нам нужно закрыть месяц. У нас тариф Business, мы платим вам 30к, можно ускорить?»',
    exampleOutput: `{
  "category": "billing",
  "severity": "high",
  "sentiment": "frustrated",
  "requires_human": false,
  "suggested_assignee_role": "billing_specialist",
  "summary_50chars": "Не приходит счёт за апрель, тариф Business"
}`,
    curlSnippet: `curl https://api.brikko.ru/v1/chat/completions \\
${COMMON_CURL_HEADERS}
  -d '{
    "model": "auto:cheap",
    "messages": [
      { "role": "system", "content": "<system prompt из этой страницы>" },
      { "role": "user", "content": "<тело тикета>" }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": <schema из этой страницы>
    }
  }'`,
  },

  // 5
  {
    slug: 'medical-record-extractor',
    shortTitle: 'Извлечение данных из медкарты',
    title: 'Извлечение данных из медкарты (с PII-маскингом)',
    metaDescription:
      'Свободный текст медкарты → структурированные симптомы, диагноз, препараты. С автоматическим PII-маскингом перед отправкой в LLM. JSON Schema рецепт api.brikko.ru.',
    intro:
      'Свободный приём врача → структурированные поля: симптомы, диагноз, назначения. ФИО пациента, телефоны, СНИЛС автоматически маскируются Brikko перед отправкой в модель.',
    description:
      'Brikko перед отправкой в LLM подменяет ФИО, телефоны, паспортные данные на placeholder вида {NAME_1}, {PHONE_1} (см. секцию «PII-compliance» на главной). После ответа модели placeholder восстанавливается обратно. Модель не видит реальные ПДн пациента — schema-валидный JSON приходит уже с реальными значениями.',
    schemaName: 'medical_record_extractor',
    jsonSchema: `{
  "name": "medical_record_extractor",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["symptoms", "diagnosis", "prescribed_drugs", "follow_up_required"],
    "properties": {
      "symptoms": {
        "type": "array",
        "items": { "type": "string" },
        "maxItems": 20
      },
      "diagnosis": { "type": "string" },
      "prescribed_drugs": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["name", "dosage"],
          "properties": {
            "name": { "type": "string" },
            "dosage": { "type": "string" }
          }
        }
      },
      "follow_up_required": { "type": "boolean" }
    }
  }
}`,
    systemPrompt: `Ты — медицинский ассистент по структурированию записей приёма. На вход — свободный текст приёма. На выход — JSON по схеме medical_record_extractor.

Правила:
- symptoms — короткие фразы на русском, 2-4 слова каждая.
- diagnosis — формулировка ближе к МКБ, или свободная если в тексте нет точной.
- prescribed_drugs — название препарата + дозировка (например, "Парацетамол", "500 мг 3 раза в день").
- follow_up_required — true, если назначен повторный приём или контрольный анализ.`,
    exampleInput:
      '«Пациент {NAME_1}, 45 лет. Жалобы на головную боль 4 дня, температуру 37.8, заложенность носа. Осмотр: горло гиперемировано. Диагноз — острый ринофарингит. Назначено: парацетамол 500 мг при температуре, аквалор по 2 пшика 3 р/д. Повторный приём через 7 дней.»',
    exampleOutput: `{
  "symptoms": [
    "головная боль",
    "температура 37.8",
    "заложенность носа",
    "гиперемия горла"
  ],
  "diagnosis": "Острый ринофарингит",
  "prescribed_drugs": [
    { "name": "Парацетамол", "dosage": "500 мг при температуре" },
    { "name": "Аквалор", "dosage": "2 пшика 3 раза в день" }
  ],
  "follow_up_required": true
}`,
    curlSnippet: `curl https://api.brikko.ru/v1/chat/completions \\
${COMMON_CURL_HEADERS}
  -d '{
    "model": "auto:smart",
    "messages": [
      { "role": "system", "content": "<system prompt из этой страницы>" },
      { "role": "user", "content": "<текст приёма; ФИО пациента передавай как есть — Brikko замаскирует>" }
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": <schema из этой страницы>
    }
  }'`,
    notes:
      'PII-маскинг включён по умолчанию для всех тарифов. Brikko заменяет ФИО, телефоны, СНИЛС, паспорт, ИНН на placeholder-токены до отправки в LLM, и восстанавливает их в ответе. Логирование промптов отключено по умолчанию (см. секцию «Как Brikko защищает персональные данные» на главной).',
  },
];

export function getRecipe(slug: string): Recipe | undefined {
  return RECIPES.find((r) => r.slug === slug);
}
