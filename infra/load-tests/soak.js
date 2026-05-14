// LT-4 — Soak test. 200 RPM × 24 часа.
// Goal: обнаружить memory leaks, deadlocks, slow degradation.

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('voltari_errors');

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-arrival-rate',
      rate: 200,
      timeUnit: '1m',
      duration: '24h',
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.001'],   // 0.1% устойчиво
    voltari_errors: ['rate<0.001'],
    http_req_duration: ['p(95)<800'],  // p95 не должен расти в течение суток
  },
};

const BASE_URL = __ENV.BASE_URL || 'https://staging-api.brikko.ru';
const API_KEY = __ENV.API_KEY;
if (!API_KEY) throw new Error('API_KEY env var required');

export default function () {
  // 50% non-stream, 50% stream — реалистичная пропорция
  const useStream = Math.random() < 0.5;

  const payload = JSON.stringify({
    model: 'auto:cheap',
    messages: [{ role: 'user', content: 'reply with one word' }],
    max_tokens: 10,
    stream: useStream,
  });

  const r = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers: {
      'Authorization': `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
      'Accept': useStream ? 'text/event-stream' : 'application/json',
    },
    timeout: '30s',
    tags: { kind: useStream ? 'stream' : 'nonstream' },
  });

  const ok = check(r, {
    'status 200': (resp) => resp.status === 200,
  });
  errorRate.add(!ok);
}

// Каждый час — снапшот метрик в файл (для anomaly-trend анализа)
export function handleSummary(data) {
  return {
    'stdout': summarize(data),
    'soak-final.json': JSON.stringify(data, null, 2),
  };
}

function summarize(data) {
  const m = data.metrics;
  return `
=== LT-4 Soak Summary (24h) ===
Total req: ${m.http_reqs?.values?.count}
p50: ${m.http_req_duration?.values?.['p(50)']?.toFixed(0)}ms
p95: ${m.http_req_duration?.values?.['p(95)']?.toFixed(0)}ms
p99: ${m.http_req_duration?.values?.['p(99)']?.toFixed(0)}ms
Errors: ${((m.http_req_failed?.values?.rate || 0) * 100).toFixed(3)}%

Сверь с node_exporter:
- gateway_memory_rss за 24ч
- pg_stat_database.numbackends за 24ч
- redis_memory_used_bytes за 24ч

Если рост >5% — memory leak; runbook §profiling.
================================
`;
}
