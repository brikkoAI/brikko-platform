import { BRAND } from '@/lib/brand';

export const metadata = {
  title: 'Реквизиты исполнителя',
  description:
    'Реквизиты исполнителя услуг Brikko: ФИО, ИНН, контакты, статус самозанятого (плательщика НПД).',
};

/**
 * Публичная страница с реквизитами исполнителя — для проверки ЮKassa,
 * договорной работы с клиентами-юрлицами и стандартного compliance.
 *
 * Источник данных — `lib/brand.ts`. При смене статуса (самозанятый → ИП → ООО)
 * правим только brand.ts, эта страница автоматически обновится.
 */
export default function LegalInfoPage() {
  return (
    <section className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold tracking-tight text-fg-primary">
        Реквизиты исполнителя
      </h1>
      <p className="mt-3 text-body text-fg-muted">
        Информация для заключения договоров, выставления счетов и подтверждения
        легитимности оплат.
      </p>

      <dl className="mt-10 grid grid-cols-1 gap-6 rounded-2xl border border-[var(--hairline)] bg-[var(--bg-elevated)] p-8 sm:grid-cols-[200px_1fr]">
        <DefinitionRow term="ФИО исполнителя" value={BRAND.legalEntity ?? '—'} />
        <DefinitionRow term="Налоговый статус" value={BRAND.legalStatus ?? '—'} />
        <DefinitionRow term="ИНН" value={BRAND.inn ?? '—'} mono />
        <DefinitionRow term="Email для связи" value={BRAND.supportEmail} link={`mailto:${BRAND.supportEmail}`} />
        <DefinitionRow term="Телефон" value={BRAND.phone ?? '—'} />
        <DefinitionRow term="Сайт" value={BRAND.domain} link={`https://${BRAND.domain}`} />
        <DefinitionRow term="API endpoint" value={BRAND.apiDomain} mono link={`https://${BRAND.apiDomain}`} />
      </dl>

      <h2 className="mt-12 text-2xl font-semibold tracking-tight text-fg-primary">
        Чем занимается исполнитель
      </h2>
      <p className="mt-3 text-body text-fg-muted">
        Исполнитель оказывает услуги доступа к API больших языковых моделей (LLM)
        через единый OpenAI-совместимый шлюз <strong>Brikko</strong>: маршрутизация
        запросов между провайдерами (OpenAI, Anthropic, Google, DeepSeek, YandexGPT,
        GigaChat), биллинг по факту использования токенов, выдача закрывающих
        документов в соответствии с режимом налогообложения исполнителя.
      </p>

      <h2 className="mt-12 text-2xl font-semibold tracking-tight text-fg-primary">
        Налогообложение и документы для клиентов
      </h2>
      <p className="mt-3 text-body text-fg-muted">
        Исполнитель применяет специальный налоговый режим «Налог на профессиональный
        доход» (НПД) в соответствии с Федеральным законом № 422-ФЗ от 27.11.2018.
      </p>
      <ul className="mt-4 space-y-2 text-body text-fg-muted">
        <li>
          <strong>Чек самозанятого</strong> — формируется автоматически после каждой
          оплаты в приложении «Мой налог» ФНС. Электронный чек отправляется на email,
          указанный при регистрации, и доступен в личном кабинете на странице
          «Документы».
        </li>
        <li>
          <strong>Оплата НДС</strong> — не предусмотрена режимом НПД (статья 2
          Федерального закона № 422-ФЗ). Цены на сайте указаны без НДС.
        </li>
        <li>
          <strong>Договор-оферта</strong> — публикуется на странице{' '}
          <a href="/legal/oferta" className="brikko-link">
            /legal/oferta
          </a>{' '}
          и считается заключённым с момента акцепта (первой оплаты или регистрации
          в личном кабинете).
        </li>
      </ul>

      <h2 className="mt-12 text-2xl font-semibold tracking-tight text-fg-primary">
        Связь и претензии
      </h2>
      <p className="mt-3 text-body text-fg-muted">
        Все обращения, претензии, заявления о возврате средств, запросы на удаление
        персональных данных направлять на{' '}
        <a
          href={`mailto:${BRAND.supportEmail}`}
          className="brikko-link"
        >
          {BRAND.supportEmail}
        </a>
        . Срок ответа — не более 10 рабочих дней с момента получения обращения.
      </p>
      <p className="mt-3 text-body text-fg-muted">
        Адрес для корреспонденции и оригиналов документов сообщается по запросу через
        email.
      </p>

      <h2 className="mt-12 text-2xl font-semibold tracking-tight text-fg-primary">
        Связанные документы
      </h2>
      <ul className="mt-4 space-y-2 text-body">
        <li>
          <a href="/legal/oferta" className="brikko-link">
            Публичная оферта
          </a>{' '}
          — условия оказания услуг
        </li>
        <li>
          <a href="/legal/privacy" className="brikko-link">
            Политика конфиденциальности
          </a>{' '}
          — обработка персональных данных по 152-ФЗ
        </li>
        <li>
          <a href="/legal/cookie" className="brikko-link">
            Cookie
          </a>{' '}
          — политика использования cookie
        </li>
      </ul>

      <p className="mt-12 text-body-sm text-fg-faint">
        Дата последнего обновления реквизитов: 2026-05-01.
      </p>
    </section>
  );
}

function DefinitionRow({
  term,
  value,
  link,
  mono,
}: {
  term: string;
  value: string;
  link?: string;
  mono?: boolean;
}) {
  const content = link ? (
    <a href={link} className="brikko-link">
      {value}
    </a>
  ) : (
    value
  );

  return (
    <>
      <dt className="text-body-sm font-medium uppercase tracking-wide text-fg-faint">
        {term}
      </dt>
      <dd className={`text-body text-fg-primary ${mono ? 'font-mono' : ''}`}>
        {content}
      </dd>
    </>
  );
}
