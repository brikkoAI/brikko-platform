import Link from 'next/link';

export const metadata = {
  title: '404 — Страница не найдена',
  description: 'Возможно, вы перешли по битой ссылке.',
};

/**
 * Глобальный 404 для путей вне marketing-сегмента (auth-страницы, /app/* через
 * ошибочный URL и т.д.). Marketing имеет собственный not-found.tsx с layout-shell'ом.
 *
 * Cream Studio v6 (Sprint 13.7, P1, 2026-05-06):
 *   - Серверфонные tokens (--bg-base / --fg-primary / --hairline) — работает в обеих
 *     темах автоматически через [data-theme].
 *   - Editorial typography: «404» крупным сериф-числом, H1 в Source Serif, lede в Geist.
 *   - 2 CTA: «На главную» (primary) → / и «Документация» (secondary) → /docs.
 *
 * Без иконок и декора: 404 — состояние ошибки, любой визуальный шум здесь
 * читается как «всё ещё что-то загружается». Чистая типографика — четкий сигнал
 * «это не баг, это просто нет страницы».
 */
export default function NotFoundPage() {
  return (
    <main
      role="main"
      style={{
        minHeight: '100vh',
        background: 'var(--bg-base)',
        color: 'var(--fg-primary)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '80px 24px',
        textAlign: 'center',
      }}
    >
      <div style={{ maxWidth: 560 }}>
        <p
          style={{
            fontFamily: 'Geist Mono, JetBrains Mono, ui-monospace, monospace',
            fontSize: 12,
            letterSpacing: '0.22em',
            textTransform: 'uppercase',
            color: 'var(--fg-faint)',
            margin: 0,
          }}
        >
          Error · 404
        </p>

        <h1
          style={{
            fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
            fontWeight: 400,
            fontSize: 'clamp(40px, 7vw, 72px)',
            lineHeight: 1.05,
            letterSpacing: '-0.02em',
            color: 'var(--fg-primary)',
            margin: '20px 0 16px',
          }}
        >
          404 — <span style={{ fontStyle: 'italic' }}>Страница</span> не найдена
        </h1>

        <p
          style={{
            fontSize: 17,
            lineHeight: 1.55,
            color: 'var(--fg-muted)',
            margin: '0 auto 32px',
            maxWidth: '40ch',
          }}
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
      </div>
    </main>
  );
}
