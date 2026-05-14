'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * RoutingAnimation — overlay-анимация «traveling dot + pulse» поверх RoutingDiagram.
 *
 * Что делает: маленькая точка-«пакет» бежит из блока «Твой код» → «API» (там
 * пульс) → одна из 6 моделей (там highlight), пауза 0.7s, повтор.
 *
 * Реализация:
 *   - Координаты считываются один раз через `getBoundingClientRect` + ResizeObserver
 *     по data-атрибутам существующих DOM-нод. Структуру SVG мы не трогаем.
 *   - Анимация — чистый CSS keyframes с GPU-only свойствами (transform, opacity).
 *     Никаких setInterval-ов, дёргающих React; единственный таймер — смена
 *     `targetIdx` каждые 2.8s, синхронно с длительностью CSS-цикла.
 *   - `prefers-reduced-motion: reduce` → возвращаем null, никаких таймеров.
 *
 * SSR-safe: на первом рендере (до useLayoutEffect) ничего не отрисовываем,
 * поэтому в HTML от сервера нет ни анимации, ни случайных значений → mismatch
 * исключён.
 */
const CYCLE_MS = 2800;

type Coords = {
  startX: number;
  startY: number;
  apiX: number;
  apiY: number;
  apiW: number;
  apiH: number;
  models: { x: number; y: number; w: number; h: number }[];
};

export function RoutingAnimation({ modelCount }: { modelCount: number }) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<Coords | null>(null);
  const [targetIdx, setTargetIdx] = useState(0);
  const [enabled, setEnabled] = useState(false);

  // Проверка prefers-reduced-motion + первичный mount.
  // Делаем в useEffect, не useLayoutEffect, чтобы не блокировать paint.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setEnabled(!mq.matches);
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, []);

  // Измерение координат DOM-нод. useLayoutEffect, чтобы успеть до первого paint
  // и точку не «дёргало» от 0,0 к финальной позиции.
  useLayoutEffect(() => {
    if (!enabled) return;
    const root = rootRef.current;
    if (!root) return;
    const parent = root.parentElement;
    if (!parent) return;

    const measure = () => {
      const parentRect = parent.getBoundingClientRect();
      const codeEl = parent.querySelector<HTMLElement>('[data-anim-role="code"]');
      const apiEl = parent.querySelector<HTMLElement>('[data-anim-role="api"]');
      const modelEls = parent.querySelectorAll<HTMLElement>('[data-anim-model]');
      if (!codeEl || !apiEl || modelEls.length === 0) return;

      const codeRect = codeEl.getBoundingClientRect();
      const apiRect = apiEl.getBoundingClientRect();

      const next: Coords = {
        startX: codeRect.right - parentRect.left,
        startY: codeRect.top + codeRect.height / 2 - parentRect.top,
        apiX: apiRect.left + apiRect.width / 2 - parentRect.left,
        apiY: apiRect.top + apiRect.height / 2 - parentRect.top,
        apiW: apiRect.width,
        apiH: apiRect.height,
        models: Array.from(modelEls).map((el) => {
          const r = el.getBoundingClientRect();
          return {
            x: r.left + r.width / 2 - parentRect.left,
            y: r.top + r.height / 2 - parentRect.top,
            w: r.width,
            h: r.height,
          };
        }),
      };
      setCoords(next);
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(parent);
    return () => ro.disconnect();
  }, [enabled]);

  // Смена целевой модели каждый цикл. Псевдо-рандом, но не повторяет предыдущую.
  // Не зависит от `coords` намеренно: ResizeObserver обновляет coords достаточно
  // часто (drag/resize окна), и перезапуск таймера сбивал бы фазу анимации.
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => {
      setTargetIdx((prev) => {
        if (modelCount <= 1) return 0;
        let next = Math.floor(Math.random() * modelCount);
        if (next === prev) next = (next + 1) % modelCount;
        return next;
      });
    }, CYCLE_MS);
    return () => window.clearInterval(id);
  }, [enabled, modelCount]);

  // До hydration / при reduce-motion ничего не рисуем — статичный SVG-блок
  // (Hero RSC) полностью покрывает функциональный layout.
  if (!enabled) {
    return <div ref={rootRef} aria-hidden="true" className="hidden" />;
  }

  if (!coords) {
    return (
      <div
        ref={rootRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
      />
    );
  }

  const target = coords.models[targetIdx] ?? coords.models[0];
  if (!target) {
    return (
      <div
        ref={rootRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
      />
    );
  }

  // CSS-переменные → координаты передаются в keyframes без перегенерации стилей.
  const styleVars = {
    '--brikko-start-x': `${coords.startX}px`,
    '--brikko-start-y': `${coords.startY}px`,
    '--brikko-api-x': `${coords.apiX}px`,
    '--brikko-api-y': `${coords.apiY}px`,
    '--brikko-target-x': `${target.x}px`,
    '--brikko-target-y': `${target.y}px`,
  } as React.CSSProperties;

  return (
    <div
      ref={rootRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
      style={styleVars}
    >
      <style>{ANIMATION_CSS}</style>

      {/* API pulse-кольцо — поверх API-блока, синхронизировано на 30-37% цикла.
          key={targetIdx} перезапускает анимацию с 0% синхронно с dot и highlight,
          иначе пульс «плавал бы» относительно прилёта точки. */}
      <span
        key={`pulse-${targetIdx}`}
        className="brikko-anim-api-pulse absolute rounded-lg ring-2 ring-brand-600"
        style={{
          left: coords.apiX - coords.apiW / 2 - 2,
          top: coords.apiY - coords.apiH / 2 - 2,
          width: coords.apiW + 4,
          height: coords.apiH + 4,
        }}
      />

      {/* Highlight-кольцо на текущей model-цели — на 65-75% цикла */}
      <span
        key={`highlight-${targetIdx}`}
        className="brikko-anim-target-highlight absolute rounded-md ring-2 ring-brand-500"
        style={{
          left: target.x - target.w / 2 - 1,
          top: target.y - target.h / 2 - 1,
          width: target.w + 2,
          height: target.h + 2,
        }}
      />

      {/* Точка-«пакет». key включает targetIdx, чтобы при смене цели
          анимация перестартовывала с фазы 1 — иначе бы точка телепортировалась
          в середине цикла. */}
      <span
        key={`dot-${targetIdx}`}
        className="brikko-anim-dot absolute h-2 w-2 rounded-full bg-brand-700"
      />
    </div>
  );
}

/**
 * Все keyframes в одной строке: точка + два пульса. Длительность одна (2.8s),
 * фазы внутри keyframes управляют opacity, чтобы pulse и highlight появлялись
 * только в нужные временные окна.
 *
 * Точка движется по тайм-карте:
 *   0.00 → 0.30  code → api    (ease-in-out через cubic-bezier на доле transform)
 *   0.30 → 0.37  пауза у api   (pulse)
 *   0.37 → 0.65  api → target  (ease-in-out)
 *   0.65 → 0.75  пауза у target (highlight)
 *   0.75 → 1.00  invisible idle (gap 700ms перед следующим циклом)
 *
 * Натуральный ease-in-out симулируем через близкие промежуточные ключи (12.5%,
 * 17.5% / 47.5%, 57.5%) — чистый linear-translate в keyframes выглядит «дёрганно»;
 * пара промежуточных точек даёт визуальное замедление у цели.
 */
const ANIMATION_CSS = `
@keyframes brikko-anim-dot-travel {
  0% {
    transform: translate3d(calc(var(--brikko-start-x) - 4px), calc(var(--brikko-start-y) - 4px), 0);
    opacity: 0;
  }
  3% {
    opacity: 1;
  }
  15% {
    transform: translate3d(
      calc((var(--brikko-start-x) + var(--brikko-api-x)) / 2 - 4px),
      calc((var(--brikko-start-y) + var(--brikko-api-y)) / 2 - 4px),
      0
    );
    opacity: 1;
  }
  30%, 37% {
    transform: translate3d(calc(var(--brikko-api-x) - 4px), calc(var(--brikko-api-y) - 4px), 0);
    opacity: 1;
  }
  51% {
    transform: translate3d(
      calc((var(--brikko-api-x) + var(--brikko-target-x)) / 2 - 4px),
      calc((var(--brikko-api-y) + var(--brikko-target-y)) / 2 - 4px),
      0
    );
    opacity: 1;
  }
  65%, 72% {
    transform: translate3d(calc(var(--brikko-target-x) - 4px), calc(var(--brikko-target-y) - 4px), 0);
    opacity: 1;
  }
  78% {
    opacity: 0;
  }
  100% {
    transform: translate3d(calc(var(--brikko-target-x) - 4px), calc(var(--brikko-target-y) - 4px), 0);
    opacity: 0;
  }
}

@keyframes brikko-anim-api-pulse-kf {
  0%, 28% {
    transform: scale(1);
    opacity: 0;
  }
  33% {
    transform: scale(1.03);
    opacity: 0.9;
  }
  39% {
    transform: scale(1);
    opacity: 0;
  }
  100% {
    transform: scale(1);
    opacity: 0;
  }
}

@keyframes brikko-anim-target-highlight-kf {
  0%, 63% {
    opacity: 0;
  }
  68% {
    opacity: 1;
  }
  77% {
    opacity: 0;
  }
  100% {
    opacity: 0;
  }
}

.brikko-anim-dot {
  animation: brikko-anim-dot-travel 2800ms ease-in-out infinite;
  filter: drop-shadow(0 0 4px rgba(67, 56, 202, 0.5));
  will-change: transform, opacity;
}

.brikko-anim-api-pulse {
  animation: brikko-anim-api-pulse-kf 2800ms ease-out infinite;
  transform-origin: center center;
  will-change: transform, opacity;
  opacity: 0;
}

.brikko-anim-target-highlight {
  animation: brikko-anim-target-highlight-kf 2800ms ease-out infinite;
  will-change: opacity;
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .brikko-anim-dot,
  .brikko-anim-api-pulse,
  .brikko-anim-target-highlight {
    animation: none !important;
    opacity: 0 !important;
  }
}
`;
