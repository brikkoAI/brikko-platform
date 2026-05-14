'use client';

/**
 * ProofCounters — Cream Studio v6 (Sprint 13, 2026-05-02; refresh 2026-05-09).
 *
 * 4 ключевых числа: 38 моделей / 6 провайдеров / 200 ₽ welcome / 99.9% PII
 * recall. Анимация count-up на entry в viewport (IntersectionObserver).
 * Большие numerals в Source Serif 4 ~120-144px, italic-units, fg-primary.
 *
 * UX: glance-test проверяет можно ли за 1 секунду понять proposition
 * страницы — четыре цифры дают это без чтения параграфов. Четвёртый
 * счётчик добавлен с появлением observability-фич (BrikkoLens + 152-ФЗ
 * маскинг).
 *
 * `prefix` / `suffix` поддерживают «+» и «%» вокруг цифры; `decimals=1`
 * нужен для 99.9 (recall маскинга) — count-up округляется до 1 знака.
 */

import { useEffect, useRef, useState } from 'react';

interface CounterDef {
  target: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  unit: string;
  label: string;
}

const COUNTERS: CounterDef[] = [
  {
    target: 38,
    unit: 'моделей',
    label: 'OpenAI, Anthropic, Google, DeepSeek, Yandex, Sber — все в одном API.',
  },
  {
    target: 6,
    unit: 'провайдеров',
    label: 'Smart routing с failover за <2 секунды. Один упал — переключаемся.',
  },
  {
    target: 200,
    unit: '₽ welcome',
    label: 'Welcome-бонус новому аккаунту. Хватает на ~1 000 запросов к GPT-5 mini.',
  },
  {
    target: 99.9,
    decimals: 1,
    suffix: '%',
    unit: 'PII recall',
    label: 'ФИО, паспорта, телефоны и карты — маскируются перед отправкой за рубеж.',
  },
];

export function ProofCounters() {
  return (
    <section
      id="proof"
      style={{
        position: 'relative',
        zIndex: 2,
        padding: '120px 8vw',
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 40,
          maxWidth: 1280,
          margin: '0 auto',
        }}
        className="brikko-proof-row"
      >
        {COUNTERS.map((c) => (
          <Counter key={c.unit + c.target} def={c} />
        ))}
      </div>
      <style>{`
        @media (max-width: 1023px) {
          .brikko-proof-row { grid-template-columns: repeat(2, 1fr) !important; gap: 48px !important; }
        }
        @media (max-width: 640px) {
          .brikko-proof-row { grid-template-columns: 1fr !important; gap: 48px !important; }
        }
      `}</style>
    </section>
  );
}

function Counter({ def }: { def: CounterDef }) {
  const [value, setValue] = useState(0);
  const ref = useRef<HTMLDivElement | null>(null);
  const playedRef = useRef(false);
  const decimals = def.decimals ?? 0;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      setValue(def.target);
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !playedRef.current) {
            playedRef.current = true;
            const start = performance.now();
            const dur = 900;
            const factor = Math.pow(10, decimals);
            const tick = (now: number) => {
              const t = Math.min(1, (now - start) / dur);
              const eased = 1 - Math.pow(1 - t, 3);
              const raw = def.target * eased;
              setValue(Math.round(raw * factor) / factor);
              if (t < 1) requestAnimationFrame(tick);
              else setValue(def.target);
            };
            requestAnimationFrame(tick);
          }
        });
      },
      { threshold: 0.5 },
    );

    io.observe(el);
    return () => io.disconnect();
  }, [def.target, decimals]);

  const display = value.toLocaleString('ru-RU', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

  return (
    <div ref={ref} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ width: 28, height: 1, background: 'var(--fg-primary)', marginBottom: 4 }} />
      <div
        style={{
          fontFamily: '"Source Serif 4", "PP Editorial New", Georgia, serif',
          fontWeight: 400,
          // 4 колонки → ужимаем шрифт сравнительно с 3-колоночной версией.
          fontSize: 'clamp(56px, 7vw, 112px)',
          lineHeight: 0.92,
          letterSpacing: '-0.04em',
          color: 'var(--fg-primary)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {def.prefix}
        {display}
        {def.suffix}
        <span
          style={{
            fontStyle: 'italic',
            color: 'var(--fg-muted)',
            fontSize: '0.42em',
            marginLeft: 8,
            letterSpacing: '-0.02em',
            display: 'block',
            marginTop: 2,
          }}
        >
          {def.unit}
        </span>
      </div>
      <div style={{ fontSize: 14, color: 'var(--fg-muted)', lineHeight: 1.5, maxWidth: '24ch' }}>
        {def.label}
      </div>
    </div>
  );
}
