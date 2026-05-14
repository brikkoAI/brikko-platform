/**
 * FAQ — Cream Studio v6 (Sprint 13, 2026-05-02).
 *
 * Native <details>/<summary> + grayscale-styling. A11y из коробки.
 * Контент — те же 9 вопросов из 05_Marketing/02_landing_copy.md §7,
 * адаптированные под актуальный moat.
 */

export function FAQ() {
  return (
    <section id="faq" className="brikko-section">
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 48px',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          FAQ
        </span>
        <h2 className="brikko-h2">
          <span>Частые </span>
          <span className="brikko-h2-italic">вопросы</span>
        </h2>
      </header>

      <div
        style={{
          maxWidth: 880,
          margin: '0 auto',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <div
          style={{
            border: '1px solid var(--hairline)',
            borderRadius: 24,
            background: 'var(--bg-base)',
            overflow: 'hidden',
            transition: 'border-color 400ms var(--ease-in-out-quart), background 400ms var(--ease-in-out-quart)',
          }}
        >
          {QUESTIONS.map((q, i) => (
            <details
              key={q.q}
              className="brikko-faq-item"
              style={{
                padding: '24px 28px',
                borderBottom: i === QUESTIONS.length - 1 ? 'none' : '1px solid var(--hairline)',
              }}
            >
              <summary
                style={{
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'flex-start',
                  justifyContent: 'space-between',
                  gap: 16,
                  fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
                  fontWeight: 400,
                  fontSize: 19,
                  lineHeight: 1.35,
                  color: 'var(--fg-primary)',
                  letterSpacing: '-0.005em',
                  listStyle: 'none',
                }}
              >
                <span>{q.q}</span>
                <Plus />
              </summary>
              <div
                style={{
                  marginTop: 14,
                  fontSize: 15,
                  lineHeight: 1.6,
                  color: 'var(--fg-muted)',
                }}
              >
                {q.a}
              </div>
            </details>
          ))}
        </div>
      </div>

      <style>{`
        .brikko-faq-item summary::-webkit-details-marker { display: none; }
        .brikko-faq-item summary { list-style: none; }
        .brikko-faq-item[open] .brikko-faq-plus {
          transform: rotate(45deg);
        }
        .brikko-faq-plus {
          flex-shrink: 0;
          margin-top: 6px;
          color: var(--fg-faint);
          transition: transform 240ms var(--ease-out-quint), color 240ms var(--ease-out-quint);
        }
        .brikko-faq-item summary:hover .brikko-faq-plus {
          color: var(--fg-primary);
        }
        .brikko-faq-item code {
          font-family: var(--font-mono), 'JetBrains Mono', ui-monospace, monospace;
          font-size: 13px;
          padding: 1px 6px;
          border-radius: 4px;
          background: var(--bg-elevated);
          color: var(--fg-primary);
          border: 1px solid var(--hairline);
        }
      `}</style>
    </section>
  );
}

function Plus() {
  return (
    <svg
      className="brikko-faq-plus"
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M10 4v12M4 10h12"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

type QA = { q: string; a: React.ReactNode };

const QUESTIONS: readonly QA[] = [
  {
    q: 'Почему не использовать дешёвый прокси к OpenAI?',
    a: 'Brikko публикует прайс каждой модели в рублях открыто в каталоге — реальная стоимость видна до запроса, не из счетов. Большинство прокси в РФ цены не публикуют. И второе: даже у тех прокси, что научились выдавать документы, нет автоматического failover между провайдерами — если OpenAI упал, ваше приложение лежит. У Brikko 6 провайдеров и переключение за <2 секунды.',
  },
  {
    q: 'Что если OpenAI или другой провайдер упал?',
    a: 'Brikko мониторит статус 6 провайдеров (OpenAI, Anthropic, Google, DeepSeek, YandexGPT, GigaChat). Если основной провайдер начинает отдавать 5xx или таймаутить — рутер за <2 секунды переключает запрос на резервного с похожими капабилити. Заголовок ответа `x-router-decision` показывает кто реально ответил.',
  },
  {
    q: 'Какие документы я получу при оплате?',
    a: 'Чек самозанятого (НПД) — формируется автоматически через ФНС «Мой налог» после каждого пополнения через ЮKassa. Приходит на email и доступен в кабинете на /app/billing. Это единственный финансовый документ. Для УСН-учёта чек НПД — допустимая первичка (Письмо ФНС от 16.09.2021 № АБ-4-20/13183).',
  },
  {
    q: 'Подходит ли Brikko крупному бизнесу с ЭДО и актами?',
    a: 'Если вам нужен двусторонний договор, акт оказания услуг, УПД, обмен через ЭДО (Контур.Диадок, СБИС, 1С) или НДС-вычет — Brikko в текущей форме не подходит. Мы работаем как самозанятый (НПД) и эти документы не выдаём — это сознательное ограничение. Напишите на support@brikko.ru, обсудим как можем помочь сейчас и сориентируем по срокам перехода в формат ИП.',
  },
  {
    q: 'Можно ли получить НДС-вычет?',
    a: 'Нет. Самозанятый по НПД (ст. 2 ФЗ-422) НДС не начисляет и не выделяет — счёт-фактуру выдать не можем. Расходы можно учесть на УСН по чеку, НДС-вычет невозможен. Если вычет критичен — выберите поставщика на ОСН с НДС.',
  },
  {
    q: 'Это правда легально? На каком основании работаете?',
    a: 'Исполнитель — российский самозанятый (НПД, ст. 2 ФЗ-422), ИНН и реквизиты — на /legal/info. Условия — публичная оферта на /legal/oferta. Платежи только через ЮKassa: банковские карты, СБП, T-Pay, SberPay (без иностранных карт и крипты). Чек НПД формируется автоматически после каждой оплаты. На Pro Privacy доступен PII-маскинг с гарантией 99.9% перед отправкой во внешние LLM.',
  },
  {
    q: 'Что с безопасностью данных? Куда уходят промпты?',
    a: 'Запросы проходят через нашу инфраструктуру в РФ к API провайдера. На Pro Privacy перед отправкой в зарубежные LLM ПДн (ФИО, телефоны, email, паспорта, ИНН, СНИЛС, банковские карты) автоматически заменяются на placeholder и восстанавливаются в ответе — recall маскинга 99.9% на нашем бенчмарке (см. /benchmarks/pii). Содержимое запросов и ответов не сохраняем; в логах — метаданные.',
  },
  {
    q: 'Что такое BrikkoLens?',
    a: (
      <>
        Встроенная observability вашего API-ключа. На <code>/app/traces</code> —
        лог каждого запроса за 8 недель: модель, провайдер, токены, стоимость в
        рублях, latency, статус, флаги stream/tools/cache/PII. На{' '}
        <code>/app/analytics</code> — KPI-дашборд: запросы, расход, средний
        latency, доля ошибок, графики по дням и топ-моделей. Подключать Datadog
        или Grafana не нужно — всё работает из коробки на любом тарифе.
      </>
    ),
  },
  {
    q: 'Соответствуете ли вы 152-ФЗ?',
    a: 'Да. Запросы идут через нашу инфраструктуру в РФ. Перед отправкой в зарубежные LLM (OpenAI, Anthropic, Google) данные клиента — ФИО, телефоны, email, паспорта, ИНН, СНИЛС, номера карт — автоматически заменяются на placeholder и восстанавливаются в ответе. Recall маскинга — 99.9% на нашем бенчмарке (см. /benchmarks/pii). Если ПДн совсем не должны покидать РФ — используйте YandexGPT или GigaChat: эти модели хостятся внутри страны, маршрут не выходит за российский периметр.',
  },
  {
    q: 'Где смотреть метрики моих запросов?',
    a: (
      <>
        Три страницы в кабинете. <code>/app/traces</code> — построчный лог всех
        вызовов через ваш ключ за 8 недель. <code>/app/analytics</code> —
        агрегированные KPI: расход в рублях, средний latency, доля ошибок,
        разбивка по моделям и провайдерам, графики по дням.{' '}
        <code>/app/status</code> — live-статус 6 LLM-провайдеров и нашей
        инфраструктуры. Дополнительно в каждом ответе API возвращается заголовок{' '}
        <code>x-router-decision</code> — какая модель реально ответила и почему.
      </>
    ),
  },
  {
    q: 'Как мигрировать со старого прокси? Сколько времени займёт?',
    a: 'Если у вас уже OpenAI SDK или совместимый клиент — меняете base_url на https://api.brikko.ru/v1 и ключ на наш. Всё. Стриминг работает, формат запроса/ответа идентичен. Для большинства проектов — 5-10 минут.',
  },
  {
    q: 'Чем умный рутер отличается от обычного выбора модели?',
    a: 'Вы отправляете запрос с model: "auto" или auto:cheap / auto:smart / auto:fast / auto:code. Brikko анализирует длину контекста, тип задачи и сам выбирает модель из 38 доступных. В заголовке x-router-decision приходит имя выбранной модели и причина.',
  },
  {
    q: 'Что доступно сразу, что появится позже?',
    a: 'Сразу: chat completions (sync + streaming), весь каталог моделей от OpenAI/Anthropic/Google/DeepSeek/Yandex/Sber, smart routing, PII-маскинг 99.9% (Pro Privacy), биллинг с автоматическим чеком НПД, BrikkoLens (traces + analytics). В ближайшем релизе: function calling, prompt caching, embeddings API. На roadmap: vision, audio, agents builder.',
  },
  {
    q: 'Какие модели доступны?',
    a: '38 моделей от 6 провайдеров: OpenAI (15 — GPT-5.5/5.5 Pro, GPT-5, o3, o4-mini, o1*, GPT-4o*), Anthropic (6 — Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 + legacy 3.5/3), Google (7 — Gemini 3.1 Pro, 3 Flash, 2.5 Pro/Flash, 1.5 семейство), DeepSeek (5 — V4 Pro/Flash, R1, V3.2), Yandex (2 — YandexGPT 5.1 Pro и 5 Lite), Sber (3 — GigaChat 2 Max/Pro/Lite). Используешь model: "auto:cheap" — рутер сам выбирает дешёвую модель по типу задачи. Полный каталог с ценами — на /models.',
  },
];
