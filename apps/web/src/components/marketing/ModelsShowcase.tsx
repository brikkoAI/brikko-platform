import Link from 'next/link';

/**
 * ModelsShowcase — Cream Studio v6 (Sprint 13, 2026-05-02; refresh 2026-05-11).
 *
 * 6 hand-picked models from active providers + 3 китайских provider'а
 * (Moonshot/MiniMax/Zhipu) в pending-секции, готовы в backend и
 * включатся при появлении UnionPay-ключа в env. Внизу — 3 модальных
 * Coming Soon (TTS, DALL-E, Realtime).
 *
 * UX:
 *   - 6 hand-picked = достаточно показать многообразие активных провайдеров,
 *     полный каталог 38 моделей — на /models.
 *   - Pending-providers секция показывает CEO-roadmap прозрачно: бренд
 *     поднимается до полноценного провайдера ещё до момента flip'а ключа,
 *     при этом badge «UnionPay · Q2 2026» снимает риск ожидания.
 *   - Soon-карточки модальностей (TTS/image/realtime) — отдельная секция,
 *     визуально отделена delimiter'ом.
 *
 * Цены — RUB/1M по открытому прайсу Brikko (rub = usd × 80 × 1.15,
 * см. apps/web/src/lib/models-fallback.ts).
 */

type ShowcaseModel = {
  id: string;
  provider: string;
  inputRub: number;
  outputRub: number;
};

const MODELS: ShowcaseModel[] = [
  { id: 'GPT-5.5', provider: 'OpenAI', inputRub: 460, outputRub: 2760 },
  { id: 'Claude Opus 4.7', provider: 'Anthropic', inputRub: 1380, outputRub: 6900 },
  { id: 'Gemini 3.1 Pro', provider: 'Google', inputRub: 184, outputRub: 1104 },
  { id: 'DeepSeek V4 Flash', provider: 'DeepSeek', inputRub: 13, outputRub: 26 },
  { id: 'YandexGPT 5.1 Pro', provider: 'Яндекс', inputRub: 604, outputRub: 604 },
  { id: 'GigaChat 2 Max', provider: 'Сбер', inputRub: 1283, outputRub: 1283 },
];

type PendingProvider = {
  provider: string;
  example: string;
  models: string;
  note: string;
  badge: string;
};

// Провайдеры готовы в backend (catalog.py + provider adapters), но keys пока
// не в env — нужен UnionPay-аккаунт у CEO. Прайс показываем когда CEO
// зафиксирует — сейчас pending-cards без чисел, чтобы не делать обещаний
// которые могут не сойтись при flip'е. Каталог: 4 Moonshot + 4 MiniMax +
// 4 Zhipu = 12 моделей расширения, при flip'е /v1/models/public автоматически
// начнёт их отдавать без правок на фронте.
const PENDING_PROVIDERS: PendingProvider[] = [
  {
    provider: 'Moonshot',
    example: 'Kimi K2',
    models: '4 модели · 200K ctx',
    note: 'Китайский frontier-провайдер. Открытый рассуждающий Kimi K2 + moonshot-v1-* линейка.',
    badge: 'Скоро · Q2 2026',
  },
  {
    provider: 'MiniMax',
    example: 'Hailuo M1',
    models: '4 модели · 1M ctx',
    note: 'M1 reasoning + abab6.5* chat. 1M контекст с расширенной памятью.',
    badge: 'Скоро · Q2 2026',
  },
  {
    provider: 'Zhipu (GLM)',
    example: 'GLM-4.5',
    models: '4 модели · 128K ctx',
    note: 'Флагман GLM-4.5 + GLM-4-Long (1M ctx) + GLM-4V-Plus с vision.',
    badge: 'Скоро · Q2 2026',
  },
];

type SoonModel = {
  id: string;
  provider: string;
  modality: string;
  price: string;
  note: string;
  badge: string;
};

const SOON: SoonModel[] = [
  // Whisper STT убран из Soon: вышел в Live (POST /v1/audio/transcriptions
  // отвечает в проде, проверено 2026-05-10).  Упомянут в подзаголовке
  // секции и доступен через основной API без отдельной карточки.
  {
    id: 'OpenAI TTS-1',
    provider: 'OpenAI',
    modality: 'text→audio',
    price: '1200 коп / 1M chars',
    note: 'Синтез речи. Estimated Q3 2026.',
    badge: 'Скоро · Q3 2026',
  },
  {
    id: 'DALL-E 3',
    provider: 'OpenAI',
    modality: 'text→image',
    price: '~3.2 ₽ за картинку',
    note: 'Генерация изображений. На повестке.',
    badge: 'Скоро · Q3 2026',
  },
  {
    id: 'Realtime API',
    provider: 'OpenAI',
    modality: 'voice agent',
    price: 'tba',
    note: 'WebSocket-streaming для voice-агентов.',
    badge: 'Скоро · Q4 2026',
  },
];

export function ModelsShowcase() {
  return (
    <section id="models" className="brikko-section">
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 64px',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Каталог
        </span>
        <h2 className="brikko-h2">
          <span>38 моделей. </span>
          <span className="brikko-h2-italic">Один</span> Bearer.
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          OpenAI, Anthropic, Google, DeepSeek, Яндекс, Сбер — шесть провайдеров,
          один OpenAI-совместимый ключ, плюс Whisper STT.
          Прайс в рублях за миллион токенов, открытый каталог по каждой модели.
        </p>
      </header>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
      >
        {MODELS.map((m) => (
          <div key={m.id} className="brikko-card-outer">
            <div className="brikko-card-inner">
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: '0.2em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-faint)',
                  marginBottom: 24,
                }}
              >
                {m.provider}
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 500,
                  color: 'var(--fg-primary)',
                  letterSpacing: '-0.01em',
                  marginBottom: 36,
                }}
              >
                {m.id}
              </div>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 13,
                  color: 'var(--fg-muted)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {m.inputRub} / {m.outputRub} ₽ за 1M
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Pending-providers rail — китайская frontier-линейка (Moonshot/MiniMax/
          Zhipu) подключена в backend и ждёт UnionPay-ключа CEO. Показываем
          бренды как полноценных провайдеров с badge'ом, чтобы не выглядели
          как second-tier add-on. */}
      <div
        aria-hidden="true"
        style={{
          maxWidth: 1400,
          margin: '64px auto 32px',
          padding: '0 6vw',
          display: 'flex',
          alignItems: 'center',
          gap: 20,
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
        <span
          style={{
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 10,
            fontWeight: 500,
            letterSpacing: '0.2em',
            textTransform: 'uppercase',
            color: 'var(--fg-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          На подключении · ещё 3 провайдера
        </span>
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
      </div>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
      >
        {PENDING_PROVIDERS.map((p) => (
          <div
            key={p.provider}
            className="brikko-card-outer"
            aria-disabled="true"
          >
            <div
              className="brikko-card-inner"
              style={{ position: 'relative' }}
            >
              <span
                style={{
                  position: 'absolute',
                  top: 14,
                  right: 14,
                  display: 'inline-flex',
                  padding: '2px 8px',
                  background: 'var(--bg-base)',
                  border: '1px solid var(--hairline)',
                  borderRadius: 9999,
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 9,
                  fontWeight: 500,
                  letterSpacing: '0.15em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-muted)',
                  whiteSpace: 'nowrap',
                }}
              >
                {p.badge}
              </span>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: '0.2em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-faint)',
                  marginBottom: 24,
                }}
              >
                {p.provider}
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 500,
                  color: 'var(--fg-primary)',
                  letterSpacing: '-0.01em',
                  marginBottom: 12,
                }}
              >
                {p.example}
              </div>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 13,
                  color: 'var(--fg-muted)',
                  marginBottom: 16,
                }}
              >
                {p.models}
              </div>
              <div
                style={{
                  paddingTop: 14,
                  borderTop: '1px solid var(--hairline)',
                  fontSize: 13,
                  lineHeight: 1.45,
                  color: 'var(--fg-muted)',
                }}
              >
                {p.note}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div
        aria-hidden="true"
        style={{
          maxWidth: 1400,
          margin: '64px auto 32px',
          padding: '0 6vw',
          display: 'flex',
          alignItems: 'center',
          gap: 20,
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
        <span
          style={{
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 10,
            fontWeight: 500,
            letterSpacing: '0.2em',
            textTransform: 'uppercase',
            color: 'var(--fg-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          Скоро в каталоге
        </span>
        <span style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
      </div>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 16,
          position: 'relative',
          zIndex: 2,
        }}
      >
        {SOON.map((m) => (
          <div
            key={m.id}
            className="brikko-card-outer"
            style={{
              background: 'var(--bg-tier-2)',
              opacity: 0.85,
              cursor: 'not-allowed',
            }}
            aria-disabled="true"
          >
            <div
              className="brikko-card-inner"
              style={{ background: 'var(--bg-tier-2)', position: 'relative' }}
            >
              <span
                style={{
                  position: 'absolute',
                  top: 14,
                  right: 14,
                  display: 'inline-flex',
                  padding: '2px 8px',
                  background: 'var(--bg-base)',
                  border: '1px solid var(--hairline)',
                  borderRadius: 9999,
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 9,
                  fontWeight: 500,
                  letterSpacing: '0.15em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-muted)',
                  whiteSpace: 'nowrap',
                }}
              >
                {m.badge}
              </span>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 10,
                  fontWeight: 500,
                  letterSpacing: '0.2em',
                  textTransform: 'uppercase',
                  color: 'var(--fg-faint)',
                  marginBottom: 24,
                }}
              >
                {m.provider} · {m.modality}
              </div>
              <div
                style={{
                  fontSize: 18,
                  fontWeight: 500,
                  color: 'var(--fg-primary)',
                  letterSpacing: '-0.01em',
                  marginBottom: 16,
                }}
              >
                {m.id}
              </div>
              <div
                style={{
                  fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
                  fontSize: 13,
                  color: 'var(--fg-muted)',
                }}
              >
                {m.price}
              </div>
              <div
                style={{
                  marginTop: 16,
                  paddingTop: 14,
                  borderTop: '1px solid var(--hairline)',
                  fontSize: 13,
                  lineHeight: 1.45,
                  color: 'var(--fg-muted)',
                }}
              >
                {m.note}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          maxWidth: 1400,
          margin: '56px auto 0',
          padding: '0 6vw',
          display: 'flex',
          justifyContent: 'flex-start',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <Link
          href="/models"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 10,
            fontSize: 15,
            fontWeight: 500,
            color: 'var(--fg-muted)',
            textDecoration: 'none',
            letterSpacing: '-0.005em',
            padding: '10px 0',
            borderBottom: '1px solid var(--hairline)',
          }}
        >
          Открыть полный каталог
          <ArrowIcon />
        </Link>
      </div>
    </section>
  );
}

function ArrowIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
