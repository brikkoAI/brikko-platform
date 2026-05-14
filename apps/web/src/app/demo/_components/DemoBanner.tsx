import Link from 'next/link';

/**
 * DemoBanner — Cream Studio v6 (Sprint 13.8 landing refresh, 2026-05-09).
 *
 * Тонкая полоса вверху demo-страниц: маркирует «это пример, а не реальные
 * данные» + ведёт на /signup. Тёмная (warm-gray-800) сама по себе и в light, и
 * в dark-теме — это намеренный contrast-flag для not-real-data.
 *
 * Mobile: текст переносится на новую строку, CTA становится блоком ниже,
 * чтобы tap-target оставался ≥44px.
 */
export function DemoBanner() {
  return (
    <div
      style={{
        background: '#1c1917',
        color: '#f5f5f4',
        borderBottom: '1px solid rgba(245, 245, 244, 0.12)',
      }}
    >
      <div
        className="brikko-demo-banner-inner"
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '12px 6vw',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <p
          style={{
            margin: 0,
            fontSize: 13,
            lineHeight: 1.5,
            color: '#d6d3d1',
          }}
        >
          <strong style={{ color: '#f5f5f4', fontWeight: 600 }}>Демо-данные.</strong>{' '}
          Это пример того, как выглядит панель управления у клиентов Brikko.
        </p>
        <Link
          href="/signup"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 16px',
            borderRadius: 9999,
            background: '#f5f5f4',
            color: '#1c1917',
            fontSize: 13,
            fontWeight: 600,
            textDecoration: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          Зарегистрироваться
          <ArrowRight />
        </Link>
      </div>

      <style>{`
        @media (max-width: 640px) {
          .brikko-demo-banner-inner {
            flex-direction: column;
            align-items: flex-start !important;
            gap: 10px !important;
          }
        }
      `}</style>
    </div>
  );
}

function ArrowRight() {
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
      aria-hidden="true"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
