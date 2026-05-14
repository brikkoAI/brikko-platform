import type { Metadata } from 'next';
import Link from 'next/link';
import type { Route } from 'next';
import { ShieldCheck, FileText, Mail, ArrowRight, Check, X } from 'lucide-react';
import { Lead152fzForm } from '@/components/marketing/Lead152fzForm';

/**
 * /legal/152-fz — lead-magnet landing для compliance / CTO / юристов.
 *
 * Sprint 14, 2026-05-07. CEO-бриф: см. чат от 2026-05-05 ("/legal/152-fz lead-magnet").
 *
 * Аудитория страницы (purchase-side, НЕ developer):
 *   - Compliance officer / DPO в компании-заказчике (хочет sanity-check vendor'а).
 *   - CTO / тех-директор, который должен подписать контракт с AI-vendor.
 *   - Внутренний юрист / legal counsel.
 *
 * Чем отличается от /docs/compliance/152-fz (developer-config):
 *   - Здесь — позиция продукта, due-diligence чеклист, FAQ для compliance,
 *     lead-форма. Без кода и API-параметров.
 *   - /docs/compliance/152-fz (стоит ComingSoon-заглушка) — для разработчика:
 *     как настроить маскинг, какие категории детектятся, формат API. Когда
 *     этот раздел будет готов — здесь меняется ссылка в §«Технические детали».
 *
 * Wireframe / IA:
 *   1. Hero: H1 + lede + 2 CTA (PDF / форма).
 *   2. TL;DR-карточка (4 пункта, 30 секунд скана).
 *   3. §1 «Что такое 152-ФЗ применительно к LLM» (плейн ужобный текст).
 *   4. §2 «Три легальных пути» (3 карточки сравнения).
 *   5. §3 «Как Brikko решает» (4-step flow + список делаем/не делаем).
 *   6. §4 «Due-diligence чеклист — 7 вопросов» (аккордеон-список).
 *   7. §5 «FAQ для compliance» (8 Q&A).
 *   8. §6 «Получить чеклист» (Lead152fzForm).
 *   9. §7 «Для разработчика» (тонкая ссылка → /docs/compliance/152-fz).
 *
 * Tone-of-voice: серьёзно, без маркетингового хайпа. Compliance-офицер
 * sniff'нет за 2 секунды. Числа и формулировки — упрощённые, со ссылкой
 * на закон по номеру статьи; без претензий на правовое заключение.
 *
 * Disclaimers (важно):
 *   - Не претендуем на исчерпывающее юридическое заключение. Для конкретного
 *     кейса — запрос в РКН либо профильный юрист.
 *   - Цифры штрафов: 60-100к для ЮЛ за первичное нарушение (КоАП ст.13.11);
 *     до 18 млн — повторное и/или массовая утечка (новая редакция КоАП от
 *     2026-01). Не используем «18 млн» как маркетинг — пишем диапазон.
 */

export const metadata: Metadata = {
  title: '152-ФЗ и AI: коротко о том, что нужно знать compliance · Brikko',
  description:
    'Как легально использовать GPT, Claude, Gemini в РФ под 152-ФЗ. Три пути compliance, чеклист due-diligence по AI-vendor (PDF, 1 стр), ответы на 8 типовых вопросов compliance-офицера.',
  alternates: { canonical: '/legal/152-fz' },
};

const COMPLIANCE_EMAIL = 'hello@brikko.ru';
const CHECKLIST_PATH = '/legal/152-fz-due-diligence-checklist.pdf';

export default function Legal152fzPage() {
  return (
    <article className="brikko-section-narrow" style={{ padding: '64px 6vw 96px' }}>
      <Hero />
      <Tldr />
      <Section1Law />
      <Section2ThreePaths />
      <Section3Brikko />
      <Section4Checklist />
      <Section5Faq />
      <Section6Form />
      <Section7DevLink />
      <Disclaimer />
    </article>
  );
}

/* ============================================================
 * Hero
 * ============================================================ */
function Hero() {
  return (
    <header style={{ maxWidth: 720 }}>
      <p className="brikko-eyebrow">
        <span className="brikko-eyebrow-dot" aria-hidden="true" />
        152-ФЗ · для compliance / CTO / юристов
      </p>
      <h1 className="brikko-h1">
        152-ФЗ и AI: коротко о том, что нужно знать
      </h1>
      <p className="brikko-lede">
        Если ваш бизнес обрабатывает персональные данные граждан РФ и хочет
        использовать GPT, Claude, Gemini или DeepSeek — на этой странице ответ
        на главные вопросы compliance-офицера. Без воды, со ссылками на статьи
        закона.
      </p>
      <div className="mt-2 flex flex-wrap gap-3">
        <a
          href={CHECKLIST_PATH}
          className="brikko-cta-primary"
          target="_blank"
          rel="noopener noreferrer"
          download
        >
          <FileText className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Скачать чеклист (PDF, 1 стр)
        </a>
        <a href="#form" className="brikko-cta-secondary">
          Запросить разбор кейса
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </a>
      </div>
    </header>
  );
}

/* ============================================================
 * TL;DR — карточка с 4 пунктами для 30-секундного скана.
 * UX: первое, что видит compliance-офицер — должен дать суть без чтения.
 * ============================================================ */
function Tldr() {
  const items = [
    'Закон не запрещает использовать AI. Запрещает отправлять персональные данные «как есть» в зарубежные LLM без согласия и уведомления.',
    'Три легальных пути: маскировать ПДн до отправки в LLM, обезличивать на on-prem, либо получать согласие субъекта и уведомлять Роскомнадзор о трансграничной передаче.',
    'Brikko маскирует ПДн до того, как промпт уйдёт провайдеру; mapping-данные (что чем заменили) хранятся в РФ и удаляются по запросу.',
    'Чек-лист due-diligence — 7 вопросов, которые стоит задать любому AI-vendor (нам в том числе). PDF — одна страница, по ссылке выше.',
  ];
  return (
    <section
      aria-labelledby="tldr-heading"
      className="brikko-card-flat"
      style={{ marginTop: 56, padding: 28 }}
    >
      <h2 id="tldr-heading" className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
        TL;DR
      </h2>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: 'var(--fg-primary)' }}
            />
            <p className="text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
              {it}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ============================================================
 * §1 — Что говорит закон. Простыми словами + ссылки на статьи.
 * ============================================================ */
function Section1Law() {
  return (
    <section aria-labelledby="law-heading" style={{ marginTop: 96, maxWidth: 720 }}>
      <p className="brikko-eyebrow">
        <span className="brikko-eyebrow-dot" aria-hidden="true" />
        §1
      </p>
      <h2 id="law-heading" className="brikko-h2">
        Что говорит закон применительно к LLM
      </h2>
      <p className="brikko-prose mt-4">
        152-ФЗ «О персональных данных» регулирует обработку любых сведений,
        относящихся к идентифицированному физлицу: ФИО, телефон, email, адрес,
        паспорт, ИНН, медицинские данные и так далее (ст. 3). Если вы передаёте
        промпт с такими данными в OpenAI или Anthropic — вы выполняете
        трансграничную передачу персональных данных, а это требует отдельного
        набора условий.
      </p>
      <p className="brikko-prose mt-4">
        Упрощённо для большинства B2B-кейсов:
      </p>
      <ul className="mt-3 space-y-2 brikko-prose" style={{ paddingLeft: 18 }}>
        <li>
          Должна быть законная цель обработки (ст. 6) — обычно это исполнение
          договора с клиентом или законный интерес.
        </li>
        <li>
          Если данные уходят за рубеж, нужно либо <strong className="text-fg-primary">согласие
          субъекта на трансграничную передачу</strong> (ст. 12), либо{' '}
          <strong className="text-fg-primary">обезличивание</strong> до передачи (ст. 3 п. 9).
        </li>
        <li>
          Оператор обязан уведомить Роскомнадзор о намерении обрабатывать ПДн
          (ст. 22) — большинство компаний это уже делают.
        </li>
        <li>
          Должны быть приняты технические меры защиты (ст. 19): шифрование,
          контроль доступа, аудит-лог, политика хранения.
        </li>
      </ul>
      <p className="brikko-prose mt-4">
        Ответственность — административная, по ст. 13.11 КоАП. Для юрлиц штраф
        за первичное нарушение в типовом случае — от 60 до 100 тысяч рублей. За
        повторные нарушения и массовые утечки сумма может доходить до миллионов
        рублей по новой редакции КоАП от 2026 года.
      </p>
      <p className="brikko-prose mt-4 text-body-sm text-fg-faint">
        Это упрощённое изложение, не правовое заключение. Для конкретного кейса
        — обратитесь в Роскомнадзор или к профильному юристу.
      </p>
    </section>
  );
}

/* ============================================================
 * §2 — Три легальных пути.
 * 3-column сравнение «Маскинг / On-prem / Согласие+уведомление».
 * ============================================================ */
function Section2ThreePaths() {
  const paths: PathCard[] = [
    {
      title: 'Маскинг ПДн до отправки',
      kicker: 'Brikko и аналоги',
      description:
        'PII-фильтр заменяет имена, телефоны, паспорта на токены до того, как промпт уйдёт в LLM. Провайдер видит только обезличенный текст. Mapping-таблица хранится в РФ и удаляется по retention-политике.',
      pros: [
        'Внедрение за день, без перестройки ИТ',
        'Совместимо с любой LLM (OpenAI, Anthropic, Google)',
        'Сохраняется качество ответов модели',
      ],
      cons: [
        'Нужна проверка качества маскинга на ваших данных',
        'Mapping-сервис всё равно — оператор обработки',
      ],
      tone: 'brand',
    },
    {
      title: 'Обезличивание / on-prem',
      kicker: 'Свой контур',
      description:
        'Развёртывание открытой модели (Llama, Qwen, GigaChat on-prem) в собственной инфраструктуре. ПДн не покидают периметр компании, трансграничная передача отсутствует.',
      pros: [
        'Максимальный контроль, ПДн не покидают компанию',
        'Подходит под особо чувствительные данные (мед, фин)',
      ],
      cons: [
        'Дорого: серверы, GPU, DevOps-команда',
        'Открытые модели уступают GPT-5 / Claude по качеству',
        'Срок внедрения — месяцы, не дни',
      ],
      tone: 'neutral',
    },
    {
      title: 'Согласие + уведомление РКН',
      kicker: 'Полный доступ',
      description:
        'Получаете явное согласие субъекта на трансграничную передачу (ст. 12), уведомляете Роскомнадзор о намерении такой передачи. Можно отправлять данные «как есть».',
      pros: [
        'Полный доступ ко всем возможностям LLM',
        'Подходит, если у субъекта изначально есть мотив дать согласие',
      ],
      cons: [
        'Не работает в B2C-сценариях с массовым клиентом',
        'Бюрократия: согласия нужно собирать, хранить, отзывать',
        'Уведомление РКН требует отдельной формы и срока',
      ],
      tone: 'neutral',
    },
  ];
  return (
    <section aria-labelledby="paths-heading" style={{ marginTop: 96 }}>
      <div style={{ maxWidth: 720 }}>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          §2
        </p>
        <h2 id="paths-heading" className="brikko-h2">
          Три легальных пути использовать LLM с персональными данными
        </h2>
        <p className="brikko-prose mt-4">
          На практике компании выбирают один из трёх путей в зависимости от
          бюджета, чувствительности данных и технических возможностей.
        </p>
      </div>

      <div className="mt-10 grid gap-4 lg:grid-cols-3">
        {paths.map((p) => (
          <PathCardView key={p.title} card={p} />
        ))}
      </div>
    </section>
  );
}

interface PathCard {
  title: string;
  kicker: string;
  description: string;
  pros: string[];
  cons: string[];
  tone: 'brand' | 'neutral';
}

function PathCardView({ card }: { card: PathCard }) {
  const isBrand = card.tone === 'brand';
  return (
    <article
      className="brikko-card-flat"
      style={{
        padding: 24,
        borderColor: isBrand ? 'var(--fg-primary)' : 'var(--hairline)',
        background: isBrand ? 'var(--bg-elevated)' : 'var(--bg-base)',
      }}
    >
      <p className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
        {card.kicker}
      </p>
      <h3 className="mt-2 text-xl font-semibold text-fg-primary">{card.title}</h3>
      <p className="mt-3 text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
        {card.description}
      </p>

      <h4 className="mt-5 text-body-sm font-medium text-fg-primary">Плюсы</h4>
      <ul className="mt-2 space-y-1.5">
        {card.pros.map((p) => (
          <li key={p} className="flex items-start gap-2 text-body-sm text-fg-muted">
            <Check className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} aria-hidden="true" />
            <span>{p}</span>
          </li>
        ))}
      </ul>

      <h4 className="mt-4 text-body-sm font-medium text-fg-primary">Минусы</h4>
      <ul className="mt-2 space-y-1.5">
        {card.cons.map((c) => (
          <li key={c} className="flex items-start gap-2 text-body-sm text-fg-muted">
            <X className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} aria-hidden="true" />
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

/* ============================================================
 * §3 — Как Brikko решает.
 * 4-step flow + два списка «делаем / не делаем».
 * ============================================================ */
function Section3Brikko() {
  const steps = [
    {
      label: '1. Промпт от вас',
      example: '«Свяжись с Иваном Петровым, +7 999 123 45 67, паспорт 4509 123456»',
    },
    {
      label: '2. Brikko · PII-фильтр',
      example: '«Свяжись с {NAME_1}, {PHONE_1}, паспорт {DOC_1}»',
    },
    {
      label: '3. LLM-провайдер',
      example: 'OpenAI / Anthropic / Google видят только обезличенный текст',
    },
    {
      label: '4. Ответ → unmask → вы',
      example: 'Brikko подставляет реальные значения обратно в ответ',
    },
  ];
  const doList = [
    'Маскируем ФИО, телефоны, email, паспорт, ИНН, СНИЛС, адреса до отправки в LLM',
    'Храним mapping-таблицу в РФ (Yandex Cloud, российский ЦОД)',
    'Ведём аудит-лог запросов: timestamp, модель, хэш промпта (не plaintext), стоимость',
    'Удаляем mapping и логи по запросу субъекта или истечению retention',
    'Выдаём DPA (Data Processing Agreement) на тариф Business и выше',
  ];
  const dontList = [
    'Не храним plaintext-промпты по умолчанию (включается опционально)',
    'Не передаём ПДн в чистом виде в OpenAI / Anthropic / Google',
    'Не используем ваши данные для обучения моделей',
    'Не делимся данными с третьими сторонами кроме платёжных провайдеров',
  ];
  return (
    <section aria-labelledby="brikko-heading" style={{ marginTop: 96 }}>
      <div style={{ maxWidth: 720 }}>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          §3 · решение
        </p>
        <h2 id="brikko-heading" className="brikko-h2">
          Как Brikko снимает 152-ФЗ-риск
        </h2>
        <p className="brikko-prose mt-4">
          Brikko — это API-шлюз между вашим кодом и LLM. По умолчанию все промпты
          проходят через PII-фильтр: данные маскируются токенами, в LLM уходит
          обезличенный текст, в ответе токены заменяются обратно на реальные
          значения. Прозрачно для вашего приложения.
        </p>
      </div>

      <ol
        className="mt-8 grid gap-3 lg:grid-cols-4"
        aria-label="Поток обработки промпта с PII-маскингом"
      >
        {steps.map((s, i) => (
          <li
            key={s.label}
            className="brikko-card-flat"
            style={{ padding: 18 }}
          >
            <p className="text-body-sm font-medium text-fg-primary">{s.label}</p>
            <p className="mt-2 text-body-sm text-fg-muted" style={{ lineHeight: 1.5 }}>
              {s.example}
            </p>
            {i < steps.length - 1 ? (
              <span className="sr-only">далее</span>
            ) : null}
          </li>
        ))}
      </ol>

      <div className="mt-10 grid gap-6 md:grid-cols-2">
        <div
          className="brikko-card-flat"
          style={{ padding: 24, borderColor: 'var(--hairline-hi)' }}
        >
          <h3 className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
            Что мы делаем
          </h3>
          <ul className="mt-4 space-y-2.5">
            {doList.map((d) => (
              <li key={d} className="flex items-start gap-2.5 text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
                <Check className="mt-1 h-4 w-4 shrink-0" strokeWidth={2} aria-hidden="true" />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
        <div
          className="brikko-card-flat"
          style={{ padding: 24, borderColor: 'var(--hairline-hi)' }}
        >
          <h3 className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
            Что мы НЕ делаем
          </h3>
          <ul className="mt-4 space-y-2.5">
            {dontList.map((d) => (
              <li key={d} className="flex items-start gap-2.5 text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
                <X className="mt-1 h-4 w-4 shrink-0" strokeWidth={2} aria-hidden="true" />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/* ============================================================
 * §4 — Due-diligence чеклист.
 * 7 вопросов, под каждым — короткий ответ Brikko и пометка
 * «что слушать у других вендоров».
 * ============================================================ */
function Section4Checklist() {
  const items: ChecklistItem[] = [
    {
      q: '1. Где физически хранятся персональные данные при обработке через AI?',
      brikko:
        'Mapping-таблица — в Yandex Cloud (Москва, российский ЦОД). Plaintext-промпты по умолчанию не сохраняются. Метаданные запросов (без ПДн) — там же, в РФ.',
      red:
        '«Серверы в США / ЕС» в чистом виде — это уже трансграничная передача и требует отдельных оснований.',
    },
    {
      q: '2. Какие данные идут в LLM в plaintext, а какие маскируются?',
      brikko:
        'По умолчанию маскируются ФИО, телефоны, email, паспорт, ИНН, СНИЛС, адреса. Список категорий публикуется в /docs/compliance/152-fz, можно расширять. В LLM уходит обезличенный текст с токенами.',
      red:
        'Если вендор не может назвать конкретный список категорий — маскинг существует только в маркетинге.',
    },
    {
      q: '3. Как реализуется право на удаление (ст. 21 152-ФЗ)?',
      brikko:
        'Удаление по email-запросу субъекта — в течение 30 дней. Удаляются: mapping-таблица для запросов субъекта, аудит-лог, привязка email к аккаунту. Подтверждение — письмом и записью в аудит-логе.',
      red:
        '«Удалим вручную через тикет» — допустимо, но процесс должен быть документирован.',
    },
    {
      q: '4. Какова retention-политика для mapping-данных и логов?',
      brikko:
        'Mapping живёт ровно на время одного запроса (несколько секунд) и удаляется. Аудит-лог — 12 месяцев (для разбора инцидентов и налоговой), потом автоудаление.',
      red:
        '«Храним вечно для качества» — красный флаг. Вечное хранение ПДн противоречит принципу минимизации (ст. 5).',
    },
    {
      q: '5. Кто оператор: вы (заказчик) или вендор (Brikko)?',
      brikko:
        'Вы — оператор персональных данных ваших клиентов. Brikko — обработчик по поручению (ст. 6 п. 3). На тариф Business и выше подписываем DPA с зафиксированными ролями и ответственностью.',
      red:
        '«Мы не обрабатываем ПДн» при том, что данные идут через сервис, — юридическая иллюзия.',
    },
    {
      q: '6. Какой ведётся аудит-лог? Что в нём — plaintext или хэши?',
      brikko:
        'В аудит-логе: timestamp, model, токены (in/out), хэш промпта SHA-256, статус. Plaintext-содержимое не пишется. Доступ к логам — только владелец аккаунта и founder Brikko (через 2FA).',
      red:
        'Логи с plaintext-промптами без шифрования и контроля доступа — это вторая копия ПДн.',
    },
    {
      q: '7. Кто получает уведомление при инциденте утечки и в какой срок?',
      brikko:
        'Владелец аккаунта — в течение 24 часов после обнаружения, по email и через статус-страницу. Уведомление в Роскомнадзор в нашей зоне ответственности — в течение 24 часов после обнаружения (ст. 21.1).',
      red:
        'Если вендор не может назвать срок и канал уведомления — у него нет инцидент-плана.',
    },
  ];

  return (
    <section aria-labelledby="checklist-heading" style={{ marginTop: 96 }}>
      <div style={{ maxWidth: 720 }}>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          §4 · due-diligence
        </p>
        <h2 id="checklist-heading" className="brikko-h2">
          7 вопросов, которые стоит задать любому AI-vendor
        </h2>
        <p className="brikko-prose mt-4">
          Этот чеклист — не маркетинг. Это набор вопросов, которые мы советуем
          задать любому AI-vendor (включая Brikko) до подписания контракта.
          Пропуск одного из них — типичный путь к 152-ФЗ-проблемам.
        </p>
        <p className="mt-4">
          <a
            href={CHECKLIST_PATH}
            className="brikko-cta-secondary"
            target="_blank"
            rel="noopener noreferrer"
            download
          >
            <FileText className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Скачать как PDF (1 страница)
          </a>
        </p>
      </div>

      <ol className="mt-10 space-y-4" aria-label="Чеклист due-diligence по AI-vendor">
        {items.map((it) => (
          <li
            key={it.q}
            className="brikko-card-flat"
            style={{ padding: 24 }}
          >
            <h3 className="text-body-large font-semibold text-fg-primary">
              {it.q}
            </h3>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div>
                <p className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
                  Ответ Brikko
                </p>
                <p className="mt-1.5 text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
                  {it.brikko}
                </p>
              </div>
              <div>
                <p className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
                  На что обращать внимание у других
                </p>
                <p className="mt-1.5 text-body text-fg-muted" style={{ lineHeight: 1.55 }}>
                  {it.red}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

interface ChecklistItem {
  q: string;
  brikko: string;
  red: string;
}

/* ============================================================
 * §5 — FAQ для compliance.
 * Native <details>/<summary> = доступно из коробки + работает без JS.
 * ============================================================ */
function Section5Faq() {
  const items: FaqItem[] = [
    {
      q: 'Подходит ли Brikko под обработку медицинских данных и спецкатегорий ПДн (ст. 10)?',
      a: 'Спецкатегории (здоровье, биометрия, расовая принадлежность, политика, религия) требуют отдельного письменного согласия и усиленных мер защиты. По умолчанию — рекомендуем on-prem-режим (тариф Business+) либо полное обезличивание на стороне клиента до отправки в Brikko. Маскинг общего назначения для спецкатегорий — недостаточная мера.',
    },
    {
      q: 'Нужно ли уведомлять Роскомнадзор о подключении Brikko?',
      a: 'Если вы уже подали уведомление об обработке ПДн (ст. 22) и в нём перечислены ваши обработчики и цели — обновите уведомление. Brikko фигурирует как «обработчик по поручению, обеспечивающий маскирование и маршрутизацию запросов в LLM». Уведомление подаётся через сайт РКН, обработка — до 30 дней.',
    },
    {
      q: 'Можно ли получить DPA (Data Processing Agreement)?',
      a: 'Да, на тарифах Business и Business+. На Pay-as-you-go / Pro / Team действует публичная оферта (/legal/oferta) с зафиксированной ролью обработчика. Шаблон DPA — на запрос на ' + COMPLIANCE_EMAIL + '.',
    },
    {
      q: 'Что происходит, если LLM-провайдер (OpenAI, Anthropic) изменит ToS?',
      a: 'Маскинг работает на нашей стороне — провайдер не получает ПДн в любом случае, поэтому изменение ToS провайдера не меняет ваш compliance-периметр. Если провайдер уходит из-за санкций — Smart Router автоматически переключает на альтернативную модель того же класса.',
    },
    {
      q: 'Как Brikko соотносится с приказом ФСТЭК № 21 и приказом ФСБ № 378?',
      a: 'ФСТЭК-21 регулирует технические меры защиты ИСПДн в зависимости от уровня защищённости (УЗ-1…УЗ-4). Brikko предоставляет описание мер на стороне сервиса (шифрование, контроль доступа, аудит-лог, изоляция). Сертификат ФСТЭК — в нашей дорожной карте 2026-Q4. ФСБ-378 актуален при использовании криптосредств для шифрования ПДн на стороне оператора — это ваша зона.',
    },
    {
      q: 'Что с трансграничной передачей в Anthropic / OpenAI после маскирования?',
      a: 'После маскирования передаваемые данные не относятся к категории «персональные» в смысле ст. 3 — они обезличены и не позволяют идентифицировать субъект без mapping-таблицы. Наша позиция: трансграничная передача ПДн отсутствует. Это позиция, не правовое заключение РКН — для критически чувствительных кейсов рекомендуем доп. согласие субъекта или on-prem.',
    },
    {
      q: 'Какова ответственность Brikko в случае инцидента?',
      a: 'В рамках публичной оферты — возврат средств и уведомление субъектов / РКН в течение 24 часов. В рамках индивидуального DPA (Business+) — индивидуальные SLA и пределы ответственности по согласованию.',
    },
    {
      q: 'Хранятся ли логи запросов? Можно ли их выгрузить или удалить?',
      a: 'По умолчанию хранится метаданные запросов (без plaintext-содержимого) на 12 месяцев. Plaintext-логи — опциональная функция, включается осознанно в настройках аккаунта. Выгрузка логов — через личный кабинет, удаление — по запросу на ' + COMPLIANCE_EMAIL + '.',
    },
  ];
  return (
    <section aria-labelledby="faq-heading" style={{ marginTop: 96 }}>
      <div style={{ maxWidth: 720 }}>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          §5 · FAQ
        </p>
        <h2 id="faq-heading" className="brikko-h2">
          Типовые вопросы compliance-офицера
        </h2>
        <p className="brikko-prose mt-4">
          Если в этом списке нет вашего вопроса — напишите на{' '}
          <a href={`mailto:${COMPLIANCE_EMAIL}`} className="brikko-link">
            {COMPLIANCE_EMAIL}
          </a>
          , ответим в течение рабочего дня и добавим в FAQ.
        </p>
      </div>

      <div className="mt-10 grid gap-3" style={{ maxWidth: 880 }}>
        {items.map((it) => (
          <details
            key={it.q}
            className="brikko-card-flat group"
            style={{ padding: 0 }}
          >
            <summary
              className="cursor-pointer list-none px-6 py-5 text-body-large font-medium text-fg-primary marker:hidden"
              style={{ outline: 'none' }}
            >
              <span className="flex items-start justify-between gap-4">
                <span style={{ lineHeight: 1.45 }}>{it.q}</span>
                <span
                  aria-hidden="true"
                  className="mt-1 shrink-0 text-fg-faint group-open:rotate-45"
                  style={{ transition: 'transform 200ms ease' }}
                >
                  +
                </span>
              </span>
            </summary>
            <div className="px-6 pb-5">
              <p className="text-body text-fg-muted" style={{ lineHeight: 1.6 }}>
                {it.a}
              </p>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

interface FaqItem {
  q: string;
  a: string;
}

/* ============================================================
 * §6 — Lead form.
 * Anchor #form для CTA в Hero.
 * ============================================================ */
function Section6Form() {
  return (
    <section
      id="form"
      aria-labelledby="form-heading"
      style={{ marginTop: 96, scrollMarginTop: 96 }}
    >
      <div style={{ maxWidth: 720 }}>
        <p className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          §6 · разбор кейса
        </p>
        <h2 id="form-heading" className="brikko-h2">
          Получить чеклист и короткий ответ под ваш кейс
        </h2>
        <p className="brikko-prose mt-4">
          Опишите в двух словах, какие данные обрабатываете и где блок с
          152-ФЗ. Мы отвечаем письмом в течение рабочего дня и прикладываем
          PDF-чеклист (1 страница).
        </p>
      </div>

      <div className="mt-8" style={{ maxWidth: 720 }}>
        <Lead152fzForm />
      </div>
    </section>
  );
}

/* ============================================================
 * §7 — Ссылка на developer-документацию.
 * Тонкий блок, чтобы CTO мог переслать инженеру ровно туда.
 * ============================================================ */
function Section7DevLink() {
  return (
    <section
      aria-labelledby="dev-heading"
      style={{ marginTop: 96, maxWidth: 720 }}
    >
      <p className="brikko-eyebrow">
        <span className="brikko-eyebrow-dot" aria-hidden="true" />
        §7 · для разработчика
      </p>
      <h2 id="dev-heading" className="brikko-h2">
        Технические детали — отдельным разделом
      </h2>
      <p className="brikko-prose mt-4">
        Если вы CTO и хотите передать инженеру конкретику (категории детектора,
        формат API-параметров, как настроить кастомный список ПДн) — она
        собрана в отдельном разделе документации.
      </p>
      <p className="mt-5">
        <Link
          href={'/docs/compliance/152-fz' as Route}
          className="brikko-cta-secondary"
        >
          Перейти в /docs/compliance/152-fz
          <ArrowRight className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </Link>
      </p>
    </section>
  );
}

/* ============================================================
 * Disclaimer — нижний блок, мелким шрифтом.
 * UX: compliance-офицер обязан увидеть granted limitations.
 * ============================================================ */
function Disclaimer() {
  return (
    <aside
      className="brikko-card-flat"
      style={{
        marginTop: 96,
        padding: 24,
        background: 'var(--bg-elevated)',
        borderColor: 'var(--hairline)',
      }}
      aria-label="Дисклеймер"
    >
      <div className="flex items-start gap-3">
        <ShieldCheck
          className="mt-1 h-5 w-5 shrink-0"
          strokeWidth={1.75}
          aria-hidden="true"
        />
        <div>
          <p className="text-body-sm text-fg-muted" style={{ lineHeight: 1.6 }}>
            Эта страница — обзорный материал для compliance-офицера, не правовое
            заключение. Конкретные кейсы (особенно с медицинскими, финансовыми,
            детскими данными) требуют консультации с профильным юристом или
            прямого обращения в Роскомнадзор. По индивидуальным DPA и custom
            compliance-настройкам — пишите на{' '}
            <a href={`mailto:${COMPLIANCE_EMAIL}`} className="brikko-link">
              {COMPLIANCE_EMAIL}
            </a>
            .
          </p>
          <p className="mt-3 text-body-sm text-fg-faint" style={{ lineHeight: 1.5 }}>
            Последнее обновление: 2026-05-07.
          </p>
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2 text-body-sm text-fg-muted">
        <Mail className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        <a href={`mailto:${COMPLIANCE_EMAIL}?subject=152-%D0%A4%D0%97%20compliance%20%D0%B2%D0%BE%D0%BF%D1%80%D0%BE%D1%81`} className="brikko-link">
          Прямой контакт: {COMPLIANCE_EMAIL}
        </a>
      </div>
    </aside>
  );
}
