/**
 * Open Graph image — превью при шеринге в Telegram/Twitter/LinkedIn/Slack/etc.
 * 1200×630 — официальный размер OG (Facebook/Open Graph spec).
 *
 * Дизайн (Cream Studio v6): cream-фон (#F5F5F4) + espresso-блок «B» (#1C1917)
 * + brand-имя espresso + tagline. Никаких индиго/фиолетовых акцентов —
 * grayscale-палитра соответствует ребрендингу 2026-04-30.
 *
 * 2026-05-14 PII pivot: текст обновлён под новое позиционирование
 * (AI Privacy Ecosystem). TODO: replace og-image.png under PII pivot —
 * designer task. Когда дизайнер пришлёт статический PNG, положить в
 * `app/opengraph-image.png` — Next.js берёт его с приоритетом над .tsx.
 */
import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const alt = 'Brikko — AI Privacy Ecosystem: маскируем ПД перед ChatGPT, Claude, Gemini';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          background: '#F5F5F4',
          display: 'flex',
          flexDirection: 'column',
          padding: '80px',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        {/* Лого + название */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
          <div
            style={{
              width: 80,
              height: 80,
              background: '#1C1917',
              borderRadius: 16,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#F5F5F4',
              fontSize: 56,
              fontWeight: 700,
              letterSpacing: '-0.04em',
            }}
          >
            B
          </div>
          <div
            style={{
              fontSize: 56,
              fontWeight: 600,
              color: '#1C1917',
              letterSpacing: '-0.02em',
            }}
          >
            Brikko
          </div>
        </div>

        {/* Главный месседж */}
        <div
          style={{
            marginTop: 80,
            fontSize: 72,
            fontWeight: 600,
            color: '#1C1917',
            letterSpacing: '-0.03em',
            lineHeight: 1.05,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div>AI без утечки</div>
          <div>
            <span style={{ color: '#1C1917' }}>152-ФЗ.</span>
          </div>
          <div style={{ color: '#57534E', fontSize: 44, marginTop: 16 }}>
            Маскируем ПД перед ChatGPT, Claude, Gemini.
          </div>
        </div>

        {/* Footer-факты */}
        <div
          style={{
            marginTop: 'auto',
            display: 'flex',
            gap: 40,
            fontSize: 24,
            color: '#57534E',
          }}
        >
          <span>Natasha NER</span>
          <span>·</span>
          <span>ФИО · ИНН · СНИЛС · паспорт РФ</span>
          <span>·</span>
          <span>brikko.ru</span>
        </div>
      </div>
    ),
    { ...size },
  );
}
