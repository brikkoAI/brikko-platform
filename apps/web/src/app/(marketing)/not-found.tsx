import Link from 'next/link';

export const metadata = {
  title: '404 — Страница не найдена',
  description: 'Возможно, вы перешли по битой ссылке.',
};

/**
 * 404 для marketing-сегмента: получает header+footer из (marketing)/layout.tsx.
 * Без отдельного <html>/body — это segment-level not-found.
 *
 * Cream Studio v6 (Sprint 13.7, P1, 2026-05-06): editorial typography,
 * grayscale, два CTA — «На главную» + «Документация». Tokens идут через
 * --bg-base / --fg-primary, поэтому страница автоматически работает в обеих темах.
 */
export default function MarketingNotFoundPage() {
  return (
    <section
      style={{
        position: 'relative',
        minHeight: '60vh',
        padding: '120px 6vw 96px',
        maxWidth: 1400,
        margin: '0 auto',
        zIndex: 2,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        textAlign: 'center',
      }}
    >
      <span className="brikko-eyebrow">
        <span className="brikko-eyebrow-dot" aria-hidden="true" />
        Error · 404
      </span>

      <h1
        className="brikko-h1"
        style={{
          fontSize: 'clamp(40px, 6vw, 72px)',
          maxWidth: '16ch',
          marginTop: 16,
        }}
      >
        404 — <span className="brikko-h2-italic">Страница</span> не найдена
      </h1>

      <p
        className="brikko-lede"
        style={{ marginTop: 8, marginBottom: 32, maxWidth: '40ch' }}
      >
        Возможно, вы перешли по битой ссылке.
      </p>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: 12,
        }}
      >
        <Link href="/" className="brikko-btn brikko-btn-primary">
          На главную
        </Link>
        <Link href="/docs" className="brikko-btn brikko-btn-secondary">
          Документация
        </Link>
      </div>
    </section>
  );
}
