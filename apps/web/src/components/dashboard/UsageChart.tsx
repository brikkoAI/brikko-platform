'use client';

import { useMemo } from 'react';
import { formatKopecks } from '@/lib/utils';
import type { Kopecks, UsageItem } from '@/lib/types';

/**
 * Простой SVG bar-chart без внешних зависимостей. Целенаправленный выбор:
 * - Recharts/Echarts весят 40-100KB; для одного графика на /usage это много.
 * - Нам нужен только один тип графика (расход по дням) — кастомный SVG проще.
 * - В V2, когда добавим drill-down с zoom и интерактивами, переедем на Echarts.
 *
 * A11y: каждая колонка имеет <title>, чтобы screen-reader зачитал «28 апр — 124 ₽».
 */

interface UsageChartProps {
  /** Items уже сгруппированы по day, отсортированы по дате. */
  items: UsageItem[];
}

const PADDING = { top: 12, right: 12, bottom: 24, left: 40 };
const HEIGHT = 200;
const BAR_GAP = 4;

export function UsageChart({ items }: UsageChartProps) {
  const data = useMemo(
    () => items.filter((i) => i.date).map((i) => ({ date: i.date!, cost_kop: i.cost_kop })),
    [items],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center rounded-md border border-dashed border-gray-200 text-body-sm text-gray-500">
        Нет данных за выбранный период
      </div>
    );
  }

  const maxCost = Math.max(1, ...data.map((d) => d.cost_kop));
  const innerWidth = 100; // используем percent — SVG масштабируется по контейнеру
  const innerHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const barWidth = (innerWidth - BAR_GAP * (data.length - 1)) / data.length;

  const yTicks = [0, 0.5, 1].map((p) => p * maxCost);

  return (
    <div className="w-full">
      <svg
        role="img"
        aria-label="График расхода по дням"
        viewBox={`0 0 ${100 + PADDING.left + PADDING.right} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-[200px] w-full"
      >
        {/* Y-axis ticks */}
        {yTicks.map((tick) => {
          const y = PADDING.top + innerHeight - (tick / maxCost) * innerHeight;
          return (
            <g key={tick}>
              <line
                x1={PADDING.left}
                x2={PADDING.left + innerWidth + PADDING.right}
                y1={y}
                y2={y}
                stroke="rgb(226 232 240)"
                strokeWidth="0.4"
              />
              <text
                x={PADDING.left - 4}
                y={y + 3}
                textAnchor="end"
                fontSize="8"
                fill="rgb(100 116 139)"
              >
                {formatKopecks(Math.round(tick) as Kopecks)}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        {data.map((d, idx) => {
          const h = (d.cost_kop / maxCost) * innerHeight;
          const x = PADDING.left + idx * (barWidth + BAR_GAP);
          const y = PADDING.top + innerHeight - h;
          return (
            <g key={d.date}>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={Math.max(h, 1)}
                fill="rgb(99 102 241)"
                opacity={0.85}
              >
                <title>{`${new Date(d.date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })} — ${formatKopecks(d.cost_kop)}`}</title>
              </rect>
            </g>
          );
        })}

        {/* X-axis labels — показываем каждый 5-й чтобы не перекрывались */}
        {data.map((d, idx) => {
          if (idx % Math.max(1, Math.floor(data.length / 6)) !== 0) return null;
          const x = PADDING.left + idx * (barWidth + BAR_GAP) + barWidth / 2;
          const label = new Date(d.date).toLocaleDateString('ru-RU', {
            day: 'numeric',
            month: 'short',
          });
          return (
            <text
              key={d.date}
              x={x}
              y={HEIGHT - 6}
              textAnchor="middle"
              fontSize="8"
              fill="rgb(100 116 139)"
            >
              {label}
            </text>
          );
        })}
      </svg>
    </div>
  );
}
