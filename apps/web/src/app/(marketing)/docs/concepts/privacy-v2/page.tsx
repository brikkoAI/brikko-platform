import Link from 'next/link';
import type { Route } from 'next';
import type { Metadata } from 'next';
import { ArrowRight } from 'lucide-react';

export const metadata: Metadata = {
  title: 'Privacy v2 — концепция PII-маскинга в Brikko · Documentation',
  description:
    'Зачем PII masking, как работает (regex + Natasha + checksum), reversible vs irreversible, сравнение с Microsoft Presidio и Skyflow. 152-ФЗ-обоснование.',
  alternates: { canonical: '/docs/concepts/privacy-v2' },
};

/**
 * /docs/concepts/privacy-v2 — UX-обоснование структуры:
 *
 *   1) Hero — еyebrow «Концепция». Эта страница НЕ reference, а explanation.
 *      Ставим разные ожидания: тут больше прозы, меньше кода.
 *   2) §1 «Зачем» — два аргумента: 152-ФЗ (compliance) и бизнес (утечки,
 *      tier-2 риски). Не один — пользователь сам определит, какой ему ближе.
 *   3) §2 «Как работает» — 3-уровневая архитектура: regex → Natasha → checksum.
 *      Не идём в код, объясняем roles на каждом уровне.
 *   4) §3 Reversible vs Irreversible — критическое различие, без которого
 *      пользователь не поймёт TTL и почему /v1/anonymize не «полная анонимизация».
 *   5) §4 Сравнение с Presidio / Skyflow / Tonic — таблица, чтобы пользователь
 *      мог обосновать выбор Brikko перед своим CTO. Без этого Brikko звучит
 *      как «ещё один прокси с regex».
 *   6) §5 Limitations — честно про false positives, false negatives, что не
 *      детектируется, что V3.
 *
 * ВАЖНО: эта страница — продающая для compliance-офицеров. Их задача —
 * убедиться, что мы не «маркетинговая обёртка», а реальная защита.
 * Поэтому — техническая глубина без воды.
 */

const COMPARE_ROWS = [
  {
    name: 'Brikko (V2)',
    coverage: '11 категорий ПД, фокус на РФ',
    russian: 'Native (Natasha + регексы под РФ-форматы)',
    reversible: 'Да, через mapping_id с TTL',
    deployment: 'SaaS в РФ (152-ФЗ периметр) + on-prem в Business+',
  },
  {
    name: 'Microsoft Presidio',
    coverage: '~25 категорий, фокус на EN/EU',
    russian: 'Слабо (нет ИНН/СНИЛС/ОГРН checksum, плохо с кириллицей в ФИО)',
    reversible: 'Нет (нужно поднимать свой mapping-store)',
    deployment: 'Self-hosted (open-source)',
  },
  {
    name: 'Skyflow Vault',
    coverage: 'Vault-первый подход (хранение токенов вместо данных)',
    russian: 'EN/EU only, нет РФ-специфичных детекторов',
    reversible: 'Да, через vault-токены',
    deployment: 'SaaS в US/EU (НЕ 152-ФЗ для РФ)',
  },
  {
    name: 'Tonic.ai',
    coverage: 'Synthetic data + masking, тяжёлый ML-pipeline',
    russian: 'Слабо',
    reversible: 'Частично',
    deployment: 'SaaS в US',
  },
];

export default function PrivacyV2DocPage() {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
      <header>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Концепция
        </p>
        <h1 className="brikko-h1 mt-3">Privacy v2 — PII-маскинг в Brikko</h1>
        <p className="brikko-lede mt-4">
          Brikko заменяет персональные данные в промпте на плейсхолдеры до того,
          как запрос уйдёт в LLM-провайдера, и восстанавливает оригиналы в
          ответе. Это снимает класс рисков: утечку через логи провайдера,
          использование данных в обучении модели, передачу за периметр РФ.
        </p>
      </header>

      <section id="why" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Зачем PII masking</h2>

        <h3 className="mt-6 text-base font-semibold text-fg-primary">
          152-ФЗ (compliance)
        </h3>
        <p className="brikko-prose mt-3">
          Закон о персональных данных РФ требует, чтобы данные граждан России
          обрабатывались на территории РФ. Запрос в OpenAI / Anthropic /
          Gemini — это передача за периметр. Если в запросе есть ФИО, ИНН,
          телефон или email клиента — это формальное нарушение, даже если
          вы получили согласие на обработку. С масированными плейсхолдерами{' '}
          <code className="brikko-code-inline">&lt;NAME_1&gt;</code>,{' '}
          <code className="brikko-code-inline">&lt;INN_1&gt;</code>{' '}
          провайдер видит обезличенные данные — это уже не передача ПД, а
          обработка обезличенной информации (ст.&nbsp;7 152-ФЗ).
        </p>

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          Бизнес-причины
        </h3>
        <p className="brikko-prose mt-3">
          Compliance — формальная сторона. Реальные риски, которые закрывает
          masking:
        </p>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            <strong className="text-fg-primary">Логи провайдера.</strong> OpenAI
            и Anthropic хранят запросы 30+ дней для abuse-detection. С masking
            в этих логах нет ваших ПД.
          </li>
          <li>
            <strong className="text-fg-primary">Training opt-out.</strong> Даже
            при включённом opt-out некоторые провайдеры используют
            «обезличенные» примеры для дообучения. Masking гарантирует, что
            ПД физически отсутствуют в payload.
          </li>
          <li>
            <strong className="text-fg-primary">Подрядчики и аудит.</strong>{' '}
            Внешний аудит (СБ, compliance) увидит в логах Brikko плейсхолдеры,
            а не данные клиентов — это упрощает доступ нескольких ролей к
            операционным дашбордам.
          </li>
          <li>
            <strong className="text-fg-primary">Уязвимости в LLM-app.</strong>{' '}
            Prompt injection / jailbreaks могут привести к выдаче содержимого
            промпта обратно пользователю. Masking ограничивает урон —
            злоумышленник получит плейсхолдеры, не оригиналы.
          </li>
        </ul>
      </section>

      <section id="how" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Как работает</h2>
        <p className="brikko-prose mt-4">
          Trinity-подход: regex для структурных форматов, Natasha для NER на
          кириллице, checksum для валидации числовых идентификаторов. Каждый
          уровень компенсирует слабости двух других.
        </p>

        <h3 className="mt-6 text-base font-semibold text-fg-primary">
          1. Regex — структурные форматы
        </h3>
        <p className="brikko-prose mt-3">
          Первый уровень. Покрывает то, что имеет жёсткий формат: телефоны,
          email, IP, паспорт (4+6 цифр), ИНН (10 или 12 цифр), СНИЛС, ОГРН,
          банковские карты. Скорость ~1ms на запрос — пренебрежимо для
          gateway, который и так ждёт LLM 1-30 секунд.
        </p>

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          2. Natasha — NER для ФИО на кириллице
        </h3>
        <p className="brikko-prose mt-3">
          ФИО на русском не имеют жёсткого формата —{' '}
          <em>«Михаил»</em> может быть именем или названием компании
          («Михаил» — частное предприятие). Regex «3 заглавные подряд» даёт{' '}
          15-20% false positive на бизнес-текстах. Natasha — open-source NER-
          библиотека, обученная на новостных корпусах русского языка —
          различает имена-сущности от других проперов с точностью ~95%.
          Скорость ~10ms на 1k токенов.
        </p>

        <h3 className="mt-8 text-base font-semibold text-fg-primary">
          3. Checksum — валидация числовых идентификаторов
        </h3>
        <p className="brikko-prose mt-3">
          ИНН, СНИЛС, ОГРН, банковские карты — у всех есть контрольная цифра
          по детерминированному алгоритму. Regex отлавливает «10 цифр»,
          checksum проверяет, что это валидный ИНН, а не номер заказа.
          Без checksum мы бы маскировали{' '}
          <code className="brikko-code-inline">«заказ #1234567890»</code> как{' '}
          <code className="brikko-code-inline">&lt;INN_1&gt;</code> — это сильно
          раздражает на бизнес-логах. С checksum false positive на
          числах падает до &lt;1%.
        </p>
      </section>

      <section id="reversible" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Reversible vs Irreversible</h2>
        <p className="brikko-prose mt-4">
          Brikko по умолчанию делает{' '}
          <strong className="text-fg-primary">reversible</strong> masking. ПД
          не уничтожаются — они заменяются на плейсхолдеры с сохранением
          mapping{' '}
          <code className="brikko-code-inline">&lt;NAME_1&gt; → «Иванов И.И.»</code>{' '}
          в Redis под уникальным{' '}
          <code className="brikko-code-inline">mapping_id</code> с TTL ~1 час.
          В ответе LLM мы заменяем плейсхолдеры обратно на оригиналы, и
          пользователь видит человеческий текст.
        </p>
        <p className="brikko-prose mt-4">
          Это сознательный design choice. Альтернатива — irreversible (хеш
          вместо плейсхолдера, mapping не сохраняется) — даёт полную
          анонимизацию, но ломает 90% продуктовых сценариев: если LLM
          процитирует <code className="brikko-code-inline">&lt;NAME_1&gt;</code>{' '}
          в ответе пользователю, тот увидит abracadabra. Reversible решает эту
          проблему ценой компромисса: TTL Redis = 1 час, потом mapping
          уничтожается. Если злоумышленник украл mapping_id — у него час, чтобы
          им воспользоваться, после — данные физически удалены.
        </p>
        <p className="brikko-prose mt-4">
          Если нужна именно irreversible-семантика (например, для логов и
          аналитики, где восстановление не нужно никогда) —{' '}
          <strong className="text-fg-primary">используйте /v1/anonymize и
          выбрасывайте mapping_id</strong>. После TTL Brikko физически
          удалит mapping. Endpoint{' '}
          <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
            anonymize
          </Link>{' '}
          вернёт masked_text — это и есть ваш конечный артефакт.
        </p>
      </section>

      <section id="compare" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Сравнение с конкурентами</h2>
        <p className="brikko-prose mt-4">
          PII-маскинг — насыщенный рынок в US/EU. В РФ выбор уже: Skyflow и
          Tonic не размещены здесь, Presidio открыт, но требует self-hosting и
          плохо знает наши форматы. Brikko занимает нишу «РФ-native + reversible
          + SaaS».
        </p>

        <div className="mt-6 overflow-x-auto rounded-2xl border border-[var(--hairline)] bg-[var(--bg-base)]">
          <table className="w-full border-collapse text-body-sm">
            <thead className="bg-[var(--bg-elevated)] text-left text-fg-primary">
              <tr>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Решение
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Покрытие
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  РФ-форматы
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Reversible
                </th>
                <th className="border-b border-[var(--hairline)] px-4 py-3 font-semibold">
                  Развёртывание
                </th>
              </tr>
            </thead>
            <tbody className="text-fg-muted">
              {COMPARE_ROWS.map((row, i) => {
                const isLast = i === COMPARE_ROWS.length - 1;
                const cellClass = isLast
                  ? 'px-4 py-3 align-top'
                  : 'border-b border-[var(--hairline)] px-4 py-3 align-top';
                return (
                  <tr key={row.name}>
                    <td className={cellClass}>
                      <strong className="font-semibold text-fg-primary">{row.name}</strong>
                    </td>
                    <td className={cellClass}>{row.coverage}</td>
                    <td className={cellClass}>{row.russian}</td>
                    <td className={cellClass}>{row.reversible}</td>
                    <td className={cellClass}>{row.deployment}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="brikko-prose mt-5">
          Если у вас уже стоит Presidio — Brikko может работать поверх него
          (как pluggable detector). Это V3-фича, пока не готова. Если нужно
          ускорить — напишите{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          .
        </p>
      </section>

      <section id="limits" className="mt-12 scroll-mt-20">
        <h2 className="brikko-h2">Ограничения</h2>
        <p className="brikko-prose mt-4">
          Maсking — не серебряная пуля. Известные ограничения V2:
        </p>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-body text-fg-muted marker:text-fg-faint">
          <li>
            <strong className="text-fg-primary">Латиница в ФИО.</strong> «John
            Smith» не распознаётся как имя — Natasha обучена на кириллических
            корпусах. В V3 — Stanza для смешанных текстов.
          </li>
          <li>
            <strong className="text-fg-primary">Адреса.</strong> Намеренно НЕ
            детектируем — false positive слишком высок («улица Ленина»
            упоминается в каждой второй бизнес-переписке).
          </li>
          <li>
            <strong className="text-fg-primary">Контекстные ПД.</strong> «Год
            рождения 1985» сам по себе — не ПД. В сочетании с городом — уже
            квази-идентификатор. Brikko не делает контекстный анализ.
          </li>
          <li>
            <strong className="text-fg-primary">Vision / multimodal.</strong>{' '}
            Маскинг работает только на текстовом контенте. ПД на скрине
            паспорта (загруженного как image) сейчас не детектируется. В V3 —
            интеграция с OCR.
          </li>
          <li>
            <strong className="text-fg-primary">Streaming output.</strong>{' '}
            При <code className="brikko-code-inline">stream: true</code>{' '}
            восстановление работает chunk-by-chunk, но если плейсхолдер
            «расколот» границей чанка — он восстановится только когда оба
            чанка склеятся. Это создаёт задержку ~2-3 чанка в редких случаях.
          </li>
        </ul>
        <p className="brikko-prose mt-5">
          Если в вашем сценарии критично что-то из этого списка — напишите{' '}
          <a href="mailto:support@brikko.ru" className="brikko-link">
            support@brikko.ru
          </a>
          . Часть лимитов закрывается custom-детекторами в Business+ тарифе.
        </p>
      </section>

      <div className="brikko-cta-card mt-16">
        <p className="flex-1 text-body text-fg-primary">
          Начать использовать PII-маскинг — флаг{' '}
          <code className="brikko-code-inline">pii_protect: true</code> в
          /v1/chat/completions или standalone{' '}
          <Link href={'/docs/api/anonymize' as Route} className="brikko-link">
            /v1/anonymize
          </Link>
          .
        </p>
        <Link href="/signup" className="brikko-cta-primary">
          <span>Получить ключ</span>
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} />
        </Link>
      </div>
    </article>
  );
}
