// LT-3 — Stress test. Ramp до 5000 RPM за 10 минут.
// Goal: НАЙТИ точку отказа. Это не gating-тест, у него нет thresholds.
// Что фиксируем: при каком RPM начались таймауты, при каком — 5xx, узкое место.

import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const errorsByPhase = new Counter('errors_by_phase');
const latencyByPhase = new Trend('latency_by_phase', true);

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-arrival-rate',
      startRate: 100,
      timeUnit: '1m',
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { duration: '2m', target: 500 },
        { duration: '2m', target: 1000 },
        { duration: '2m', target: 2000 },
        { duration: '2m', target: 3000 },
        { duration: '2m', target: 5000 },
        { duration: '2m', target: 5000 },  // sustain top
        { duration: '1m', target: 0 },
      ],
    },
  },
  // No thresholds — мы намеренно ломаем систему.
};

const BASE_URL = __ENV.BASE_URL || 'https://staging-api.brikko.ru';
const API_KEY = __ENV.API_KEY;
if (!API_KEY) throw new Error('API_KEY env var required');

export default function () {
  const r = http.post(
    `${BASE_URL}/v1/chat/completions`,
    JSON.stringify({
      model: 'auto:cheap',
      messages: [{ role: 'user', content: 'ok' }],
      max_tokens: 5,
    }),
    {
      headers: {
        'Authorization': `Bearer ${API_KEY}`,
        'Content-Type': 'application/json',
      },
      timeout: '30s',
    },
  );

  // Tag by current target — для post-mortem анализа
  const phase = `rps_${__ENV.K6_VUS_TARGET || 'unknown'}`;
  if (r.status >= 500 || r.status === 0) {
    errorsByPhase.add(1, { phase, status: String(r.status) });
  }
  latencyByPhase.add(r.timings.duration, { phase });
}

// Структурированный отчёт для post-mortem.
export function handleSummary(data) {
  const summary = {
    test: 'LT-3 stress',
    timestamp: new Date().toISOString(),
    total_requests: data.metrics.http_reqs?.values?.count,
    error_count: data.metrics.errors_by_phase?.values?.count,
    p95: data.metrics.http_req_duration?.values?.['p(95)'],
    p99: data.metrics.http_req_duration?.values?.['p(99)'],
    note: 'Анализируйте по тегам phase=rps_N в Grafana.',
  };
  return {
    'stdout': JSON.stringify(summary, null, 2),
    'stress-report.json': JSON.stringify(data, null, 2),
  };
}
