import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';
import { CodeBlock } from '@/components/docs/CodeBlock';
import { ApiTable } from '@/components/docs/ApiTable';

export const metadata: Metadata = {
  title: 'POST /v1/anonymize и /v1/restore — API Reference · Brikko',
  description:
    'Standalone PII-маскинг через Brikko: маскируйте ПД на клиенте, отправляйте санитизированный текст в любой LLM, восстанавливайте после ответа. Reversible, TTL 1 час, 152-ФЗ-friendly.',
  alternates: { canonical: '/docs/api/anonymize' },
};

/**
 * /docs/api/anonymize — UX-обоснование структуры:
 *
 *   1) Hero — два endpoint signature рядом (anonymize + restore). Они
 *      работают только парой — это надо показать сразу, не разбивая на
 *      разные страницы.
 *   2) §1 «Зачем standalone» — сразу отвечаем на главный вопрос «почему
 *      не использовать pii_protect=true в /chat?». Без этого пользователь
 *      будет в недоумении: зачем дополнительный endpoint, если есть встроенное.
 *   3) §2 Workflow с диаграммой ASCII — самый понятный способ показать
 *      4-шаговый цикл. Альтернатива (тонна прозы) — хуже.
 *   4) §3 anonymize-таблица + код, §4 restore-таблица + код. Структурно
 *      идентичны другим API-страницам — узнаваемость.
 *   5) §5 Категории ПД — таблица с примерами. Это release-критическая
 *      инфа, без неё пользователь не знает, что система НЕ детектирует
 *      «название компании» (намеренно — слишком много false positive).
 *   6) §6 Privacy notes — TTL, audit без plaintext, что не персистится.
 *      152-ФЗ compliance язык.
 *
 * ВНИМАНИЕ: контракт взят из реального apps/gateway/voltari_gateway/api/
 * anonymize.py. AnonymizeRequest, AnonymizeResponse, RestoreRequest,
 * RestoreResponse — точные модели Pydantic.
 */

const ANONYMIZE_REQUEST_ROWS = [
  {
    field: 'text',
    type: 'string',
    required: true,
    description: 'Текст для маскинга. Минимум 1 символ, до 1 MB байт (UTF-8). Превышение → 413.',
  },
  {
    field: 'ttl_seconds',
    type: 'int',
    default: '3600',
    description:
      'Сколько секунд хранить mapping в Redis. От 60 (1 минута) до 86400 (24 часа). После TTL — restore вернёт 404.',
  },
];

const ANONYMIZE_RESPONSE_EXAMPLE = `{
  "masked_text": "Свяжитесь с <NAME_1>, ИНН <INN_1>, телефон <PHONE_1>",
  "mapping_id": "f7a1c92e3b4d4e8f9a2c5b6d7e8f1234",
  "count": 3,
  "audit": [
    {"type": "NAME", "count": 1, "placeholders": ["<NAME_1>"]},
    {"type": "INN", "count": 1, "placeholders": ["<INN_1>"]},
    {"type": "PHONE", "count": 1, "placeholders": ["<PHONE_1>"]}
  ],
  "expires_at_unix": 1746543600
}`;

const RESTORE_REQUEST_ROWS = [
  {
    field: 'text',
    type: 'string',
    required: true,
    description:
      'Текст с плейсхолдерами вида <NAME_1>, <INN_1> и т.д. Может быть ответом LLM на masked-prompt или любым другим текстом со ссылками на mapping.',
  },
  {
    field: 'mapping_id',
    type: 'string',
    required: true,
    description:
      'ID mapping, полученный из /v1/anonymize. От 8 до 128 символов. 404 если не найден или истёк TTL.',
  },
];

const RESTORE_RESPONSE_EXAMPLE = `{
  "restored_text": "Свяжитесь с Ивановым Иваном, ИНН 7707083893, телефон +7 999 123 4567"
}`;

const PYTHON_WORKFLOW = `import os
import requests
from openai import OpenAI

BRIKKO_KEY = os.environ["BRIKKO_API_KEY"]
brikko_base = "https://api.brikko.ru/v1"

# Шаг 1. Маскируем ПД через Brikko.
text = (
    "Договор от Иванова Ивана Ивановича, ИНН 7707083893, "
    "тел. +7 999 123 4567. Email: ivanov@example.ru"
)
r = requests.post(
    f"{brikko_base}/anonymize",
    headers={"Authorization": f"Bearer {BRIKKO_KEY}"},
    json={"text": text, "ttl_seconds": 1800},
).json()

masked = r["masked_text"]
mapping_id = r["mapping_id"]
print(f"PII detected: {r['count']}")
# masked → "Договор от <NAME_1>, ИНН <INN_1>, тел. <PHONE_1>. Email: <EMAIL_1>"

# Шаг 2. Отправляем САНИТИЗИРОВАННЫЙ текст в любую LLM.
# Это может быть Brikko, OpenAI напрямую, локальный Llama — что угодно.
# Провайдер физически не видит оригинальные ПД.
openai = OpenAI(api_key=BRIKKO_KEY, base_url=brikko_base)
resp = openai.chat.completions.create(
    model="auto:cheap",
    messages=[
        {"role": "system", "content": "Извлеки сторону договора и контакты в JSON."},
        {"role": "user", "content": masked},
    ],
)
llm_output = resp.choices[0].message.content
# llm_output → '{"name": "<NAME_1>", "inn": "<INN_1>", "phone": "<PHONE_1>"}'

# Шаг 3. Восстанавливаем ПД в ответе.
restored = requests.post(
    f"{brikko_base}/restore",
    headers={"Authorization": f"Bearer {BRIKKO_KEY}"},
    json={"text": llm_output, "mapping_id": mapping_id},
).json()
print(restored["restored_text"])
# → '{"name": "Иванов Иван Иванович", "inn": "7707083893", ...}'`;

const PII_CATEGORIES = [
  {
    type: 'NAME',
    example: 'Иванов Иван Иванович',
    notes: 'Кириллические ФИО — 3+ слов с заглавной буквы подряд. Латиница не детектируется.',
  },
  {
    type: 'INN',
    example: '7707083893 (ЮЛ) / 366215936732 (ИП)',
    notes: 'С checksum-валидацией по алгоритму ФНС. 10-значные ЮЛ + 12-значные физлица.',
  },
  {
    type: 'SNILS',
    example: '112-233-445 95',
    notes: 'С checksum по алгоритму ПФР. Принимает форматы с дефисами и без.',
  },
  {
    type: 'OGRN',
    example: '1027700132195',
    notes: '13 цифр с checksum (контрольная цифра).',
  },
  {
    type: 'OGRNIP',
    example: '304500116000157',
    notes: '15 цифр с checksum.',
  },
  {
    type: 'PASSPORT',
    example: '4509 123456',
    notes: '4+6 цифр, формат серии-номера РФ. Загранник не детектируется.',
  },
  {
    type: 'PHONE',
    example: '+7 999 123-45-67',
    notes: 'Российские мобильные/городские: +7 / 8 / без кода. Зарубежные не детектируем.',
  },
  {
    type: 'EMAIL',
    example: 'ivanov@example.ru',
    notes: 'Стандартный RFC 5321-совместимый формат.',
  },
  {
    type: 'CARD',
    example: '4276 1234 5678 9012',
    notes: 'С Luhn-checksum. Visa, Mastercard, МИР, Maestro.',
  },
  {
    type: 'BANK_ACCOUNT',
    example: '40702810500000012345',
    notes: '20-значные расчётные счета в РФ, с базовой проверкой структуры.',
  },
  {
    type: 'IP',
    example: '192.168.1.42',
    notes: 'IPv4 + IPv6. Маскируется как косвенный идентификатор пользователя.',
  },
];

export default function AnonymizeDocPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          API Reference
        </p>
        <h1 className="brikko-h1 mt-3">Anonymize и Restore</h1>
        <div className="mt-6 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="brikko-pill font-mono">POST</span>
            <code className="font-mono text-body font-semibold text-fg-primary">
              https://api.brikko.ru/v1/anonymize
            </code>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="brikko-pill font-mono">POST</span>
            <code className="font-mono text-body font-semibold text-fg-primary">
              https://api.brikko.ru/v1/restore
            </code>
          </div>
        </div>
        <p className="brikko-lede mt-6">
          Standalone PII-маскинг как переиспользуемый primitive. Маскируете ПД
          на клиенте → отправляете санитизированный текст в любую LLM (Brikko,
          OpenAI напрямую, локальный Llama) → восстанавливаете оригиналы в
          ответе по сохранённому mapping&apos;у.
        </p>
      </header>

      <section id="why" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Зачем standalone endpoint</h2>
        <p className="brikko-prose mt-4">
          У Brikko уже есть встроенный PII-protect в{' '}
          <Link href={'/docs/api/chat-completions' as Route} className="brikko-link">
            /v1/chat/completions
          </Link>{' '}
          (флаг <code className="brikko-code-inline">pii_protect: true</code>).
          Он работает «магически»: gateway сам маскирует prompt, отправляет
          провайдеру, восстанавливает в ответе. В 80% случаев этого достаточно.
        </p>
        <p className="brikko-prose mt-4">
          Но есть сценарии, где нужен явный контроль:
        </p>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            <strong className="text-fg-primary">Не-Brikko LLM в pipeline.</strong>{' '}
            Локальный Llama, OpenAI напрямую, Claude через свой ключ — Brikko
            не видит трафик. Нужен явный шаг маскинга на клиенте.
          </li>
          <li>
            <strong className="text-fg-primary">PII не идёт в LLM вообще.</strong>{' '}
            Например, санитизация перед записью в логи / vector store /
            аналитический pipeline. LLM может не быть в цепочке вовсе.
          </li>
          <li>
            <strong className="text-fg-primary">Аудит до отправки.</strong>{' '}
            Compliance-офицеру нужно увидеть точно, какие ПД мы детектировали и
            заменили — до того, как промпт уйдёт куда-либо.
          </li>
          <li>
            <strong className="text-fg-primary">Multi-step workflows.</strong>{' '}
            Один маскинг → несколько промптов с разными моделями → один restore.
            Mapping переиспользуется, не платим за повторную детекцию.
          </li>
        </ul>
      </section>

      <section id="workflow" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Workflow</h2>
        <p className="brikko-prose mt-4">
          Цикл из 4 шагов. Mapping живёт в Redis ровно{' '}
          <code className="brikko-code-inline">ttl_seconds</code> (по умолчанию
          1 час). После TTL restore возвращает 404.
        </p>

        <div className="brikko-card-bezel mt-6">
          <div className="brikko-card-bezel-inner">
            <pre className="overflow-x-auto font-mono text-body-sm leading-6 text-fg-primary">
{`клиент          Brikko          любой LLM
  │               │                 │
  │ 1. POST /v1/anonymize           │
  │ {text: "Иванов..."}             │
  ├──────────────▶│                 │
  │               │ детектирует ПД  │
  │               │ сохраняет       │
  │               │ mapping в Redis │
  │  masked_text  │                 │
  │  mapping_id   │                 │
  │◀──────────────┤                 │
  │                                 │
  │ 2. отправка masked_text         │
  │ в любой LLM (Brikko / OpenAI / Llama)
  ├────────────────────────────────▶│
  │                                 │
  │       LLM-ответ с placeholder'ами
  │◀────────────────────────────────┤
  │                                 │
  │ 3. POST /v1/restore             │
  │ {text: llm_output, mapping_id}  │
  ├──────────────▶│                 │
  │               │ заменяет        │
  │               │ <NAME_1> → orig │
  │ restored_text │                 │
  │◀──────────────┤                 │
`}
            </pre>
          </div>
        </div>
      </section>

      <section id="anonymize" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">POST /v1/anonymize</h2>
        <h3 className="mt-6 text-base font-semibold text-fg-primary">Request</h3>
        <ApiTable rows={ANONYMIZE_REQUEST_ROWS} />

        <h3 className="mt-8 text-base font-semibold text-fg-primary">Response</h3>
        <p className="brikko-prose mt-4">
          Возвращается <code className="brikko-code-inline">mapping_id</code>{' '}
          для последующего restore, краткая аудит-сводка по типам ПД (без
          plaintext оригиналов!), и unix-timestamp когда mapping истечёт.
        </p>
        <CodeBlock label="200 OK" code={ANONYMIZE_RESPONSE_EXAMPLE} />
        <p className="brikko-prose mt-4">
          Если в тексте не найдено ни одной ПД —{' '}
          <code className="brikko-code-inline">count=0</code>,{' '}
          <code className="brikko-code-inline">mapping_id=&quot;&quot;</code>{' '}
          (пустая строка), <code className="brikko-code-inline">audit=[]</code>.
          В этом случае restore не нужен — текст уже неизменён.
        </p>
      </section>

      <section id="restore" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">POST /v1/restore</h2>
        <h3 className="mt-6 text-base font-semibold text-fg-primary">Request</h3>
        <ApiTable rows={RESTORE_REQUEST_ROWS} />

        <h3 className="mt-8 text-base font-semibold text-fg-primary">Response</h3>
        <CodeBlock label="200 OK" code={RESTORE_RESPONSE_EXAMPLE} />
        <p className="brikko-prose mt-4">
          Идемпотентно: повторный вызов с теми же входами вернёт идентичный
          результат, пока mapping живёт в Redis.{' '}
          <code className="brikko-code-inline">404</code> — если mapping_id не
          найден или истёк TTL: единственный способ восстановить — позвать{' '}
          <code className="brikko-code-inline">/v1/anonymize</code> заново на
          оригинальном тексте.
        </p>
      </section>

      <section id="example" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Полный пример</h2>
        <p className="brikko-prose mt-4">
          End-to-end Python: маскинг → отправка в LLM (через Brikko, но точно
          так же работает с любой другой) → восстановление.
        </p>
        <CodeBlock label="Python — full workflow" code={PYTHON_WORKFLOW} />
      </section>

      <section id="categories" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Категории детектируемых ПД</h2>
        <p className="brikko-prose mt-4">
          Brikko детектирует 11 категорий ПД, релевантных для российского
          бизнеса. Все категории с числовыми идентификаторами (ИНН, СНИЛС,
          ОГРН, банк-карты) проверяются по checksum — это снижает false
          positive на бизнес-числах вроде «заказ #12345».
        </p>

        <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
          <table className="w-full border-collapse text-body-sm">
            <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
              <tr>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Тип
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Пример
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Примечания
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              {PII_CATEGORIES.map((cat, i) => {
                const isLast = i === PII_CATEGORIES.length - 1;
                const cellClass = isLast
                  ? 'px-4 py-3 align-top'
                  : 'border-b border-[var(--hairline)] px-4 py-3 align-top';
                return (
                  <tr key={cat.type}>
                    <td className={cellClass}>
                      <code className="font-mono text-body-sm font-semibold text-fg-primary">
                        {cat.type}
                      </code>
                    </td>
                    <td className={cellClass}>
                      <code className="font-mono text-body-sm">{cat.example}</code>
                    </td>
                    <td className={cellClass}>{cat.notes}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="brikko-prose mt-5">
          Что мы намеренно{' '}
          <strong className="font-semibold text-fg-primary">НЕ детектируем</strong>:
          названия компаний, адреса (слишком высокий false positive на улицах),
          даты рождения (контекст-зависимо), URL, медицинские диагнозы. Если
          вам нужны эти категории — напишите{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          , обсудим custom-детекторы для вашего тарифа.
        </p>
      </section>

      <section id="privacy" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Privacy notes</h2>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            <strong className="text-fg-primary">Mapping в Redis, не в БД.</strong>{' '}
            После TTL — physically deleted. Не попадает в backup&apos;ы Postgres,
            не реплицируется в analytics.
          </li>
          <li>
            <strong className="text-fg-primary">Audit без plaintext.</strong>{' '}
            В логах и в response.audit — только тип ПД и счётчик. Оригинальные
            значения видны только владельцу mapping_id и только до истечения
            TTL.
          </li>
          <li>
            <strong className="text-fg-primary">152-ФЗ-периметр.</strong>{' '}
            Brikko-инфраструктура расположена в РФ (Yandex Cloud). Mapping
            никогда не покидает периметр, даже если LLM-провайдер — иностранный.
          </li>
          <li>
            <strong className="text-fg-primary">Биллинг.</strong>{' '}
            В MVP вызовы /v1/anonymize и /v1/restore бесплатны (promo-период).
            Подробнее —{' '}
            <Link href={'/docs/concepts/pricing' as Route} className="brikko-link">
              биллинг
            </Link>
            .
          </li>
        </ul>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Получи API-ключ — стартовые 200&nbsp;₽ в баланс. Anonymize/Restore
          в MVP бесплатны.
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}
