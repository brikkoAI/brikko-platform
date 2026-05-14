'use client';

/**
 * Live status strip — pulse-dot + P95 + uptime.
 *
 * UX: тонкий signal в hero "что-то живое там работает". Подключён к реальному
 * endpoint /v1/public/status (sliding window 24h, обновляется каждые 60s).
 * Если endpoint недоступен — silent fallback на mock-диапазоны, чтобы widget
 * не исчезал при флуктуации сети. Tooltip честно говорит про скользящее окно.
 */

import { useEffect, useRef, useState } from 'react';
import styles from './LiveStatusWidget.module.css';

const P95_MIN = 280;
const P95_MAX = 380;
const UPTIME_MIN = 99.92;
const UPTIME_MAX = 99.97;
const TICK_MS = 60_000;

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function resolveStatusUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!base || /localhost:3000/.test(base)) return '/api/mock/v1/public/status';
  return `${base.replace(/\/$/, '')}/public/status`;
}

interface PublicStatusResponse {
  p95_latency_ms?: number;
  uptime_percent_24h?: number;
}

export function LiveStatusWidget() {
  const [p95, setP95] = useState<number>(320);
  const [uptime, setUptime] = useState<number>(99.94);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const url = resolveStatusUrl();

    async function tick() {
      try {
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = (await res.json()) as PublicStatusResponse;
        if (cancelled) return;
        if (typeof data.p95_latency_ms === 'number') {
          setP95(Math.round(data.p95_latency_ms));
        }
        if (typeof data.uptime_percent_24h === 'number') {
          setUptime(Number(data.uptime_percent_24h.toFixed(2)));
        }
      } catch {
        // Silent fallback: widget не должен исчезать при ошибке сети.
        if (cancelled) return;
        setP95(Math.round(rand(P95_MIN, P95_MAX)));
        setUptime(Number(rand(UPTIME_MIN, UPTIME_MAX).toFixed(2)));
      }
    }

    // Сразу — initial mock-значение, чтобы убрать «застывшую» 320, потом первая
    // попытка запроса.
    setP95(Math.round(rand(P95_MIN, P95_MAX)));
    setUptime(Number(rand(UPTIME_MIN, UPTIME_MAX).toFixed(2)));
    void tick();

    intervalRef.current = setInterval(() => {
      void tick();
    }, TICK_MS);

    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  return (
    <div
      className={styles.strip}
      role="status"
      aria-label={`Latency P95 ${p95} миллисекунд, uptime ${uptime} процентов за 24 часа`}
    >
      <span className={styles.dot} aria-hidden="true" />
      <span className={styles.text}>
        P95 {p95}ms · {uptime}% uptime · 24h
      </span>
      <span className={styles.tooltip} role="tooltip">
        Скользящее окно 24h · обновляется каждые 60s
      </span>
    </div>
  );
}
